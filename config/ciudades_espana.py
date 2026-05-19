"""
Lista de ciudades para el barrido geográfico nacional.

Estrategia: capitales de las 50 provincias + ceutas y melillas + ciudades
de >150.000 habitantes que no sean capitales.

Google Places usa cada ciudad como "ancla" y devuelve negocios en su área
de influencia, así que NO necesitamos enumerar todos los municipios.
"""

# Las 50 capitales de provincia + Ceuta + Melilla = 52
CAPITALES_PROVINCIA = [
    {"nombre": "Madrid",                "ccaa": "Comunidad de Madrid"},
    {"nombre": "Barcelona",             "ccaa": "Cataluña"},
    {"nombre": "Valencia",              "ccaa": "Comunidad Valenciana"},
    {"nombre": "Sevilla",               "ccaa": "Andalucía"},
    {"nombre": "Zaragoza",              "ccaa": "Aragón"},
    {"nombre": "Málaga",                "ccaa": "Andalucía"},
    {"nombre": "Murcia",                "ccaa": "Región de Murcia"},
    {"nombre": "Palma de Mallorca",     "ccaa": "Islas Baleares"},
    {"nombre": "Las Palmas de Gran Canaria", "ccaa": "Canarias"},
    {"nombre": "Bilbao",                "ccaa": "País Vasco"},
    {"nombre": "Alicante",              "ccaa": "Comunidad Valenciana"},
    {"nombre": "Córdoba",               "ccaa": "Andalucía"},
    {"nombre": "Valladolid",            "ccaa": "Castilla y León"},
    {"nombre": "Vigo",                  "ccaa": "Galicia"},  # No es capital pero grande
    {"nombre": "Gijón",                 "ccaa": "Principado de Asturias"},  # ídem
    {"nombre": "A Coruña",              "ccaa": "Galicia"},
    {"nombre": "Granada",               "ccaa": "Andalucía"},
    {"nombre": "Oviedo",                "ccaa": "Principado de Asturias"},
    {"nombre": "Vitoria-Gasteiz",       "ccaa": "País Vasco"},
    {"nombre": "Pamplona",              "ccaa": "Comunidad Foral de Navarra"},
    {"nombre": "Santa Cruz de Tenerife", "ccaa": "Canarias"},
    {"nombre": "Almería",               "ccaa": "Andalucía"},
    {"nombre": "San Sebastián",         "ccaa": "País Vasco"},
    {"nombre": "Burgos",                "ccaa": "Castilla y León"},
    {"nombre": "Albacete",              "ccaa": "Castilla-La Mancha"},
    {"nombre": "Santander",             "ccaa": "Cantabria"},
    {"nombre": "Castellón de la Plana", "ccaa": "Comunidad Valenciana"},
    {"nombre": "Logroño",               "ccaa": "La Rioja"},
    {"nombre": "Badajoz",               "ccaa": "Extremadura"},
    {"nombre": "Huelva",                "ccaa": "Andalucía"},
    {"nombre": "Salamanca",             "ccaa": "Castilla y León"},
    {"nombre": "Lleida",                "ccaa": "Cataluña"},
    {"nombre": "Tarragona",             "ccaa": "Cataluña"},
    {"nombre": "Cádiz",                 "ccaa": "Andalucía"},
    {"nombre": "Jaén",                  "ccaa": "Andalucía"},
    {"nombre": "Ourense",               "ccaa": "Galicia"},
    {"nombre": "Girona",                "ccaa": "Cataluña"},
    {"nombre": "Lugo",                  "ccaa": "Galicia"},
    {"nombre": "Cáceres",               "ccaa": "Extremadura"},
    {"nombre": "Melilla",               "ccaa": "Melilla"},
    {"nombre": "Ceuta",                 "ccaa": "Ceuta"},
    {"nombre": "Toledo",                "ccaa": "Castilla-La Mancha"},
    {"nombre": "Ciudad Real",           "ccaa": "Castilla-La Mancha"},
    {"nombre": "Cuenca",                "ccaa": "Castilla-La Mancha"},
    {"nombre": "Guadalajara",           "ccaa": "Castilla-La Mancha"},
    {"nombre": "Ávila",                 "ccaa": "Castilla y León"},
    {"nombre": "Segovia",               "ccaa": "Castilla y León"},
    {"nombre": "Soria",                 "ccaa": "Castilla y León"},
    {"nombre": "Palencia",              "ccaa": "Castilla y León"},
    {"nombre": "Zamora",                "ccaa": "Castilla y León"},
    {"nombre": "León",                  "ccaa": "Castilla y León"},
    {"nombre": "Teruel",                "ccaa": "Aragón"},
    {"nombre": "Huesca",                "ccaa": "Aragón"},
    {"nombre": "Pontevedra",            "ccaa": "Galicia"},
    {"nombre": "Mérida",                "ccaa": "Extremadura"},
]

# Ciudades adicionales >150.000 habitantes (cobertura de áreas metropolitanas grandes)
CIUDADES_GRANDES = [
    {"nombre": "L'Hospitalet de Llobregat", "ccaa": "Cataluña"},
    {"nombre": "Móstoles",              "ccaa": "Comunidad de Madrid"},
    {"nombre": "Alcalá de Henares",     "ccaa": "Comunidad de Madrid"},
    {"nombre": "Fuenlabrada",           "ccaa": "Comunidad de Madrid"},
    {"nombre": "Leganés",               "ccaa": "Comunidad de Madrid"},
    {"nombre": "Getafe",                "ccaa": "Comunidad de Madrid"},
    {"nombre": "Alcorcón",              "ccaa": "Comunidad de Madrid"},
    {"nombre": "Marbella",              "ccaa": "Andalucía"},
    {"nombre": "Jerez de la Frontera",  "ccaa": "Andalucía"},
    {"nombre": "Dos Hermanas",          "ccaa": "Andalucía"},
    {"nombre": "Algeciras",             "ccaa": "Andalucía"},
    {"nombre": "Cartagena",             "ccaa": "Región de Murcia"},
    {"nombre": "Lorca",                 "ccaa": "Región de Murcia"},
    {"nombre": "Sabadell",              "ccaa": "Cataluña"},
    {"nombre": "Terrassa",              "ccaa": "Cataluña"},
    {"nombre": "Badalona",              "ccaa": "Cataluña"},
    {"nombre": "Mataró",                "ccaa": "Cataluña"},
    {"nombre": "Elche",                 "ccaa": "Comunidad Valenciana"},
    {"nombre": "Torrevieja",            "ccaa": "Comunidad Valenciana"},
    {"nombre": "Benidorm",              "ccaa": "Comunidad Valenciana"},
    {"nombre": "Reus",                  "ccaa": "Cataluña"},
    {"nombre": "Roquetas de Mar",       "ccaa": "Andalucía"},
    {"nombre": "Telde",                 "ccaa": "Canarias"},
    {"nombre": "Arona",                 "ccaa": "Canarias"},
    {"nombre": "Santiago de Compostela", "ccaa": "Galicia"},
    {"nombre": "Ferrol",                "ccaa": "Galicia"},
    {"nombre": "Torrejón de Ardoz",     "ccaa": "Comunidad de Madrid"},
    {"nombre": "Parla",                 "ccaa": "Comunidad de Madrid"},
    {"nombre": "Las Rozas de Madrid",   "ccaa": "Comunidad de Madrid"},
    {"nombre": "Pozuelo de Alarcón",    "ccaa": "Comunidad de Madrid"},
]

# Combinación: total de anclas de búsqueda
TODAS_LAS_CIUDADES = CAPITALES_PROVINCIA + CIUDADES_GRANDES

# Subconjuntos predefinidos (más prácticos en línea de comandos)
SUBCONJUNTOS = {
    "andalucia": [c for c in TODAS_LAS_CIUDADES if c["ccaa"] == "Andalucía"],
    "madrid": [c for c in TODAS_LAS_CIUDADES if c["ccaa"] == "Comunidad de Madrid"],
    "barcelona": [c for c in TODAS_LAS_CIUDADES if c["ccaa"] == "Cataluña"],
    "capitales": CAPITALES_PROVINCIA,
    "espana": TODAS_LAS_CIUDADES,
}
