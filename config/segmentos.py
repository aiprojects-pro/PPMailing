"""
Catálogo de segmentos que el sistema sabe buscar.

Cada segmento define:
  - id: identificador interno (snake_case, sin espacios)
  - nombre_humano: para logs y mensajes
  - producto_cgd: qué producto le vendemos
  - queries: variantes de búsqueda en Google Places (en castellano)
  - palabras_clave_web: tokens que, si aparecen en la web, suben el score
  - palabras_descarte: tokens que descartan el lead
  - reglas_scoring: pesos específicos por segmento
"""

SCORING_BASE = {
    "tiene_web": 15, "tiene_telefono": 10, "tiene_email": 30,
    "email_corporativo": 15, "rating_alto": 10, "muchas_resenas": 10,
    "horario_publico": 5, "es_sociedad_limitada": 5,
}


SEGMENTOS = {
    "admin_fincas": {
        "nombre_humano": "Despachos de administradores de fincas",
        "producto_cgd": "Formación LOPIVI + cursos bonificables comunidades + LMS",
        "queries": [
            "administrador de fincas", "administración de fincas",
            "gestor de comunidades de propietarios",
            "administrador de comunidades", "gestoría de fincas",
        ],
        "palabras_clave_web": ["lopivi", "comunidades", "propietarios"],
        "palabras_descarte": [],
        "reglas_scoring": SCORING_BASE,
    },

    "clubes_deportivos": {
        "nombre_humano": "Clubes deportivos",
        "producto_cgd": "LOPIVI obligatorio + bloque común enseñanzas deportivas",
        "queries": [
            "club deportivo", "club de fútbol", "club de baloncesto",
            "club de natación", "club ciclista", "club de tenis", "club de pádel",
            "club balonmano", "club atletismo", "club de gimnasia",
            "club artes marciales", "club judo karate", "club hípico",
        ],
        "palabras_clave_web": [
            "lopivi", "menores", "niños", "infantil", "escuela", "cantera",
            "categorías base", "alevín", "benjamín", "cadete", "juvenil",
        ],
        "palabras_descarte": [],
        "reglas_scoring": {**SCORING_BASE, "tiene_email": 35},
    },

    "empresas_servicios_deportivos": {
        "nombre_humano": "Empresas prestadoras de servicios deportivos",
        "producto_cgd": "LOPIVI + bloque común + LMS para sus técnicos",
        "queries": [
            "empresa servicios deportivos", "gestión deportiva",
            "gestión de instalaciones deportivas", "gestión de piscinas",
            "escuela deportiva municipal", "monitores deportivos empresa",
            "actividades deportivas extraescolares", "ocio deportivo",
        ],
        "palabras_clave_web": [
            "lopivi", "menores", "escuelas deportivas", "ayuntamiento",
            "concurso público", "licitación", "actividades extraescolares",
        ],
        "palabras_descarte": [],
        "reglas_scoring": {**SCORING_BASE, "tiene_email": 30, "es_sociedad_limitada": 10},
    },

    "campamentos_verano": {
        "nombre_humano": "Empresas de campamentos de verano",
        "producto_cgd": "Curso LOPIVI urgente",
        "queries": [
            "campamento de verano", "campamentos urbanos",
            "campamentos multiaventura", "campamentos de inglés",
            "campamento deportivo niños", "colonias de verano",
            "actividades verano niños", "summer camp España", "ludoteca de verano",
        ],
        "palabras_clave_web": [
            "lopivi", "menores", "monitores titulados",
            "delegado protección", "protocolo menores",
        ],
        "palabras_descarte": ["solo adultos", "+18", "exclusivo adultos"],
        "reglas_scoring": {
            **SCORING_BASE,
            "tiene_email": 40, "rating_alto": 15, "muchas_resenas": 15,
        },
    },

    "academias_deportivas": {
        "nombre_humano": "Academias deportivas y extraescolares",
        "producto_cgd": "LOPIVI + bloque común enseñanzas deportivas + LMS",
        "queries": [
            "academia de fútbol", "academia baloncesto", "academia de tenis",
            "academia de pádel", "escuela de natación",
            "academia gimnasia rítmica", "academia de danza", "academia ballet",
            "escuela de baile niños", "academia artes marciales niños",
            "escuela de equitación niños", "academia patinaje",
        ],
        "palabras_clave_web": [
            "lopivi", "menores", "niños", "infantil",
            "tecnificación", "iniciación deportiva",
        ],
        "palabras_descarte": ["solo adultos"],
        "reglas_scoring": SCORING_BASE,
    },

    "ludotecas_ocio_infantil": {
        "nombre_humano": "Ludotecas y centros de ocio infantil",
        "producto_cgd": "LOPIVI + formación monitores tiempo libre",
        "queries": [
            "ludoteca", "centro de ocio infantil", "casa de cumpleaños niños",
            "parque infantil cubierto", "actividades infantiles centro",
            "escuela infantil tiempo libre",
        ],
        "palabras_clave_web": [
            "lopivi", "menores", "monitores", "tiempo libre",
            "delegado protección", "talleres infantiles",
        ],
        "palabras_descarte": [],
        "reglas_scoring": SCORING_BASE,
    },

    # =========================================================================
    # AÑADIDOS EN v0.3.1
    # =========================================================================

    "asesorias": {
        "nombre_humano": "Asesorías laborales, fiscales y gestorías",
        "producto_cgd": "PROGRAMA COLABORADOR — 25% comisión sobre formación FUNDAE de empresas cliente",
        "queries": [
            "asesoría laboral", "asesoría fiscal", "gestoría administrativa",
            "asesoría empresas", "asesoría laboral fiscal contable",
            "graduado social", "asesoría contable", "consultoría laboral",
            "asesoría pymes",
        ],
        "palabras_clave_web": [
            "fundae", "formación bonificada", "crédito formativo",
            "nóminas", "seguros sociales", "rrhh",
            "subvenciones formación",
        ],
        "palabras_descarte": [],
        # En asesorías, email corporativo crítico y FUNDAE en web es señal de oro
        "reglas_scoring": {
            **SCORING_BASE,
            "tiene_email": 35, "email_corporativo": 20, "es_sociedad_limitada": 10,
        },
    },

    "centros_formacion": {
        "nombre_humano": "Centros de formación y academias (compradores de LMS)",
        "producto_cgd": "Plataforma Moodle + catálogo SCORM + creación de contenidos",
        # OJO: no buscamos academias de inglés generalistas, sino centros que dan
        # formación profesional/subvencionada y necesitan campus virtual.
        "queries": [
            "centro de formación", "academia formación profesional",
            "centro estudios", "academia formación bonificada",
            "centro formación online", "academia oposiciones",
            "centro formación trabajadores", "escuela negocios profesional",
            "academia certificados profesionalidad", "centro formación continua",
        ],
        # Que mencionen "campus virtual" / "Moodle" es SEÑAL DE ORO
        "palabras_clave_web": [
            "campus virtual", "aula virtual", "online",
            "moodle", "scorm", "lms",
            "certificado profesionalidad", "fundae",
            "ifapa", "lanbide", "soib", "sepe",
            "tutoría", "matrícula online",
        ],
        "palabras_descarte": [
            "solo presencial", "exclusivamente presencial",
        ],
        "reglas_scoring": {
            **SCORING_BASE,
            "tiene_email": 35, "email_corporativo": 20,
            "rating_alto": 5, "muchas_resenas": 5,    # importan menos en B2B serio
            "es_sociedad_limitada": 10,
        },
    },
}


def obtener_segmento(segmento_id: str) -> dict:
    if segmento_id not in SEGMENTOS:
        validos = ", ".join(SEGMENTOS.keys())
        raise ValueError(f"Segmento desconocido: '{segmento_id}'. Válidos: {validos}")
    seg = dict(SEGMENTOS[segmento_id])
    seg["id"] = segmento_id
    return seg


def listar_segmentos() -> list:
    return [
        {"id": k, "nombre": v["nombre_humano"], "producto": v["producto_cgd"]}
        for k, v in SEGMENTOS.items()
    ]
