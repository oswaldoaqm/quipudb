#include "quipudb/storage/disk_manager.hpp"

#include <cstring>
#include <ios>
#include <string>
#include <vector>

#include "quipudb/error.hpp"

namespace quipudb {

namespace {

std::string describe(const std::filesystem::path& p) { return "'" + p.string() + "'"; }

}  // namespace

DiskManager::DiskManager(std::filesystem::path path, std::size_t page_size)
    : path_(std::move(path)) {
  if (page_size < Page::kMinSize || page_size > Page::kMaxSize) {
    throw IoError("tamano de pagina invalido para " + describe(path_) + ": " +
                  std::to_string(page_size));
  }
  if (std::filesystem::exists(path_)) {
    open_existing(page_size);
  } else {
    create(page_size);
  }
}

DiskManager::~DiskManager() {
  // El destructor no puede lanzar; si el flush falla aqui ya no hay a quien
  // avisar. Quien necesite la garantia llama a flush() explicitamente.
  try {
    if (file_.is_open()) {
      write_header();
      file_.flush();
    }
  } catch (...) {
  }
}

void DiskManager::create(std::size_t page_size) {
  page_size_ = page_size;
  page_count_ = 0;
  // fstream con in|out no crea el archivo: se crea primero con out y se reabre.
  {
    std::ofstream creator(path_, std::ios::binary);
    if (!creator) throw IoError("no se pudo crear " + describe(path_));
  }
  file_.open(path_, std::ios::in | std::ios::out | std::ios::binary);
  if (!file_) throw IoError("no se pudo abrir " + describe(path_));
  // La pagina 0 completa, en ceros, con la cabecera al inicio.
  std::vector<char> zero(page_size_, '\0');
  file_.seekp(0);
  file_.write(zero.data(), static_cast<std::streamsize>(zero.size()));
  write_header();
  file_.flush();
  if (!file_) throw IoError("fallo al inicializar " + describe(path_));
}

void DiskManager::open_existing(std::size_t expected_page_size) {
  file_.open(path_, std::ios::in | std::ios::out | std::ios::binary);
  if (!file_) throw IoError("no se pudo abrir " + describe(path_));

  FileHeader h;
  file_.seekg(0);
  file_.read(reinterpret_cast<char*>(&h), sizeof h);
  if (!file_ || std::memcmp(h.magic, "QPDB", 4) != 0) {
    throw IoError(describe(path_) + " no es un archivo de paginas de QuipuDB");
  }
  if (h.version != 1) {
    throw IoError(describe(path_) + " usa la version de formato " + std::to_string(h.version) +
                  " y este core entiende la 1");
  }
  if (h.page_size != expected_page_size) {
    throw IoError(describe(path_) + " fue creado con paginas de " + std::to_string(h.page_size) +
                  " bytes y se pidio abrirlo con " + std::to_string(expected_page_size));
  }
  page_size_ = h.page_size;
  page_count_ = h.page_count;

  // Defensa contra un archivo truncado: la cabecera promete mas paginas de las
  // que hay. Se prefiere fallar al abrir que leer basura despues.
  const auto expected_bytes = static_cast<std::uintmax_t>(page_size_) * (page_count_ + 1u);
  if (std::filesystem::file_size(path_) < expected_bytes) {
    throw IoError(describe(path_) + " esta truncado: la cabecera declara " +
                  std::to_string(page_count_) + " paginas");
  }
}

void DiskManager::write_header() {
  FileHeader h;
  h.page_size = static_cast<std::uint32_t>(page_size_);
  h.page_count = page_count_;
  file_.seekp(0);
  file_.write(reinterpret_cast<const char*>(&h), sizeof h);
  if (!file_) throw IoError("fallo al escribir la cabecera de " + describe(path_));
}

PageId DiskManager::allocate_page() {
  if (page_count_ == kInvalidPage - 1) {
    throw IoError(describe(path_) + " alcanzo el maximo de paginas");
  }
  const PageId id = ++page_count_;
  Page blank(page_size_);
  seek_to(id);
  file_.write(reinterpret_cast<const char*>(blank.bytes().data()),
              static_cast<std::streamsize>(page_size_));
  if (!file_) {
    --page_count_;
    throw IoError("fallo al reservar la pagina " + std::to_string(id) + " en " + describe(path_));
  }
  write_header();
  return id;
}

void DiskManager::read_page(PageId id, Page& page) {
  check_id(id);
  check_page(page);
  seek_to(id);
  file_.read(reinterpret_cast<char*>(page.bytes().data()), static_cast<std::streamsize>(page_size_));
  if (!file_) {
    file_.clear();
    throw IoError("fallo al leer la pagina " + std::to_string(id) + " de " + describe(path_));
  }
  ++reads_;
}

void DiskManager::write_page(PageId id, const Page& page) {
  check_id(id);
  check_page(page);
  seek_to(id);
  file_.write(reinterpret_cast<const char*>(page.bytes().data()),
              static_cast<std::streamsize>(page_size_));
  if (!file_) {
    file_.clear();
    throw IoError("fallo al escribir la pagina " + std::to_string(id) + " en " + describe(path_));
  }
  ++writes_;
}

void DiskManager::read_meta(std::span<std::byte> out) {
  if (out.size() > kMetaSize) {
    throw IoError("read_meta pide " + std::to_string(out.size()) + " bytes y el area meta tiene " +
                  std::to_string(kMetaSize));
  }
  file_.seekg(static_cast<std::streamoff>(kHeaderBytes));
  file_.read(reinterpret_cast<char*>(out.data()), static_cast<std::streamsize>(out.size()));
  if (!file_) {
    file_.clear();
    throw IoError("fallo al leer el area meta de " + describe(path_));
  }
}

void DiskManager::write_meta(std::span<const std::byte> in) {
  if (in.size() > kMetaSize) {
    throw IoError("write_meta recibe " + std::to_string(in.size()) +
                  " bytes y el area meta tiene " + std::to_string(kMetaSize));
  }
  file_.seekp(static_cast<std::streamoff>(kHeaderBytes));
  file_.write(reinterpret_cast<const char*>(in.data()), static_cast<std::streamsize>(in.size()));
  if (!file_) {
    file_.clear();
    throw IoError("fallo al escribir el area meta de " + describe(path_));
  }
}

void DiskManager::flush() {
  write_header();
  file_.flush();
  if (!file_) throw IoError("fallo al vaciar " + describe(path_));
}

std::uintmax_t DiskManager::file_size() const { return std::filesystem::file_size(path_); }

void DiskManager::seek_to(PageId id) {
  file_.seekg(offset_of(id));
  file_.seekp(offset_of(id));
}

void DiskManager::check_id(PageId id) const {
  if (id == 0 || id > page_count_) {
    throw IoError("pagina " + std::to_string(id) + " fuera de rango en " + describe(path_) +
                  " (validas: 1.." + std::to_string(page_count_) + ")");
  }
}

void DiskManager::check_page(const Page& page) const {
  if (page.size() != page_size_) {
    throw IoError("la pagina tiene " + std::to_string(page.size()) + " bytes y " +
                  describe(path_) + " usa " + std::to_string(page_size_));
  }
}

}  // namespace quipudb
