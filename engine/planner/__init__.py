"""Planner: decide como ejecutar una consulta y describe el plan resultante.

El plan que se produce aqui es lo que consume el Panel de Plan de Ejecucion del
frontend (seccion 2.1.5), por eso la estructura del plan es un contrato entre
el core, el parser y el frontend, y no un detalle interno.
"""
