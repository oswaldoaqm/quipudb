"""Extraccion de caracteristicas de objetos multimedia (seccion 2.4.1).

Un objeto multimedia no entra en una columna: lo que se indexa es un vector
que lo describe. Este paquete contiene el pipeline que produce ese vector:

1. descriptores locales del objeto -- SIFT en imagenes (#176), MFCC en audio
   (#177). Cada objeto da una matriz de N x D, con N distinto en cada objeto.
2. cuantizacion con K-Means contra un codebook compartido (#178).
3. histograma de palabras con TF-IDF, que es el vector de dimension fija que
   finalmente se indexa (#181).

Nada de aqui toca el core: son funciones puras sobre archivos, asi que se
prueban sin compilar los bindings.
"""
