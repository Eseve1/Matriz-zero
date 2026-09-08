# main.py
from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from datetime import date, timedelta
import os
from cachetools import TTLCache

# --- Configuración del Proyecto y Cache ---
project_id = "eddy-sandbox-2026"
os.environ["GCLOUD_PROJECT"] = project_id
TABLA = f"{project_id}.datos_practico.datos_practico"
client = bigquery.Client()

# --- CACHE EN MEMORIA ---
# TTLCache: guarda hasta 100 reportes distintos, cada uno por 1 hora.
cache = TTLCache(maxsize=100, ttl=3600)

# --- REGLAS DEL MODELO ---
# El documento define la matriz de forma binaria: 1 = atención realizada, 0 = no realizada.
# Una visita "VISITADA PERO NO COMPLETADA" sí ocurrió (el reponedor llegó al punto de venta)
# pero no alcanzó el mínimo de minutos configurado: cuenta como ejecutada y se informa
# aparte como indicador de calidad.
ESTADOS_EJECUTADOS = {"VISITADA", "VISITADA PERO NO COMPLETADA"}
# Toda visita registrada estaba planificada, salvo las marcadas como fuera de plan.
ESTADO_FUERA_DE_PLAN = "VISITADA NO PLANIFICADA"
# Prioridad para elegir el estado a mostrar cuando hay varios registros en una misma celda.
PRIORIDAD_ESTADO = {
    "VISITADA": 4,
    "VISITADA PERO NO COMPLETADA": 3,
    "NO VISITADA": 2,
    ESTADO_FUERA_DE_PLAN: 1,
}
DIAS_ES = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

app = FastAPI(
    title="Analisis Matricial de un Proceso Industrial Automatizado",
    description="""
**Reto academico:** representar matematicamente el comportamiento de un proceso mediante
transformaciones lineales y analisis matricial para evaluar su estabilidad y funcionamiento.

**Caso de estudio:** control de atencion de puntos de venta de Industrias Venado S.A.
(registros de la aplicacion WeTrade, almacenados en BigQuery).

**Modelo:** matriz de planificacion P, matriz de ejecucion E, matriz de brecha D = P - E,
indicadores de cumplimiento y cobertura, suma de matrices, ponderacion por escalar k*E y
resolucion de sistemas de ecuaciones por Gauss-Jordan.

**Optimizacion:** cache en memoria (TTLCache) para que las consultas recurrentes se
resuelvan de forma instantanea.
    """,
    version="6.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# Permite que el navegador reutilice la respuesta sin volver a pedirla.
CACHE_HTTP = "public, max-age=600, stale-while-revalidate=3600"


# --- Endpoints de la API ---

@app.get("/", tags=["Introduccion"])
def health_check():
    return {
        "status": "ok",
        "message": "API de Analisis Matricial activa y con cache habilitado.",
        "documentacion_interactiva": "Anade '/docs' a la URL para ver e interactuar con los endpoints.",
    }


@app.get("/sistema/operadores", tags=["Configuracion del Sistema"], summary="Operadores del Sistema de Control")
def get_reponedores(response: Response):
    response.headers["Cache-Control"] = CACHE_HTTP
    cache_key = "operadores_lista"
    if cache_key in cache:
        return cache[cache_key]

    query = f"""
        SELECT DISTINCT `Nombre reponedor` AS nombre
        FROM `{TABLA}`
        WHERE `Nombre reponedor` IS NOT NULL
        ORDER BY nombre
    """
    rows = [{"nombre": r["nombre"]} for r in client.query(query)]
    cache[cache_key] = rows
    return rows


@app.get("/sistema/semanas", tags=["Configuracion del Sistema"], summary="Periodos disponibles en los datos")
def get_semanas(response: Response):
    """Semanas realmente presentes en la tabla, para no fijarlas a mano en el frontend."""
    response.headers["Cache-Control"] = CACHE_HTTP
    cache_key = "semanas_lista"
    if cache_key in cache:
        return cache[cache_key]

    query = f"""
        SELECT MIN(`Fecha inicio`) AS mn, MAX(`Fecha inicio`) AS mx
        FROM `{TABLA}`
        WHERE `Fecha inicio` IS NOT NULL
    """
    fila = list(client.query(query))[0]
    if not fila["mn"]:
        return []

    # Cada periodo agrupa 7 dias corridos a partir del primer dia con datos.
    inicio, fin = fila["mn"], fila["mx"]
    semanas, cursor, n = [], inicio, 1
    while cursor <= fin:
        semanas.append({
            "valor": cursor.isoformat(),
            "etiqueta": f"Semana {n} ({DIAS_ES[cursor.weekday()][:3]} {cursor.day:02d}-Ago)",
        })
        cursor += timedelta(days=7)
        n += 1
    cache[cache_key] = semanas
    return semanas


@app.get("/analisis/matrices-de-estado", tags=["Analisis Principal"],
         summary="Capitulo 1: Representacion Matricial del Estado del Sistema")
def get_matrices_de_estado(
    response: Response,
    operador: str = Query("2043 - SERGIO.OVANDO", description="Nombre del 'operador' a analizar."),
    fecha_semana: str = Query("TODO", description="'TODO' para todo el periodo disponible, o AAAA-MM-DD para una semana."),
):
    """Estado del sistema mediante las matrices P (Planificacion) y E (Ejecucion)."""
    response.headers["Cache-Control"] = CACHE_HTTP
    return _generar_y_procesar_matrices(operador, fecha_semana)


@app.get("/analisis/matriz-de-brecha", tags=["Analisis Principal"],
         summary="Capitulo 2: Analisis de Brecha (D = P - E)")
def get_matriz_de_brecha(
    response: Response,
    operador: str = Query("2043 - SERGIO.OVANDO"),
    fecha_semana: str = Query("TODO"),
):
    response.headers["Cache-Control"] = CACHE_HTTP
    datos = _generar_y_procesar_matrices(operador, fecha_semana)
    if datos.get("error"):
        return datos
    P, E = datos["P"], datos["E"]
    D = [[P[i][j] - E[i][j] for j in range(len(P[i]))] for i in range(len(P))]
    # Los +1 de D son tareas planificadas y no ejecutadas. Los -1 son visitas fuera de
    # plan: se informan aparte para que no se cancelen con los fallos.
    fallos = sum(1 for fila in D for v in fila if v > 0)
    fuera_de_plan = sum(1 for fila in D for v in fila if v < 0)
    return {
        "descripcion": "Analisis de Brecha D = P - E",
        "matriz_D_brecha": D,
        "total_fallos_proceso": fallos,
        "total_visitas_fuera_de_plan": fuera_de_plan,
        **datos,
    }


@app.get("/analisis/indicadores-de-gestion", tags=["Analisis Principal"],
         summary="Capitulo 3: Indicadores de Estabilidad")
def get_indicadores(
    response: Response,
    operador: str = Query("2043 - SERGIO.OVANDO"),
    fecha_semana: str = Query("TODO"),
):
    response.headers["Cache-Control"] = CACHE_HTTP
    datos = _generar_y_procesar_matrices(operador, fecha_semana)
    if datos.get("error"):
        return datos
    return {"descripcion": "Indicadores de Estabilidad", **datos["indicadores"], **datos}


@app.get("/analisis/matriz-ponderada", tags=["Analisis Adicional"],
         summary="Capitulo 4: Ponderacion de Matriz (k * E)")
def get_matriz_ponderada(
    response: Response,
    operador: str = Query("2043 - SERGIO.OVANDO"),
    fecha_semana: str = Query("TODO"),
    k: float = Query(2.5, description="Escalar de ponderacion."),
):
    response.headers["Cache-Control"] = CACHE_HTTP
    datos = _generar_y_procesar_matrices(operador, fecha_semana)
    if datos.get("error"):
        return datos
    E = datos["E"]
    kE = [[round(v * k, 2) for v in fila] for fila in E]
    return {
        "descripcion": "Ponderacion por Escalar k*E",
        "escalar_k": k,
        "matriz_kE_ponderada": kE,
        "suma_kE": round(sum(sum(f) for f in kE), 2),
        **datos,
    }


@app.get("/analisis/suma-de-matrices", tags=["Analisis Adicional"],
         summary="Capitulo 5: Suma de Matrices (E1 + E2)")
def get_suma_matrices(
    response: Response,
    operador: str = Query("2043 - SERGIO.OVANDO"),
    semana_a: str = Query("2026-08-01", description="Primer periodo (AAAA-MM-DD)."),
    semana_b: str = Query("2026-08-08", description="Segundo periodo (AAAA-MM-DD)."),
):
    """Suma de ejecuciones de dos periodos: frecuencia acumulada de atencion por punto de venta."""
    response.headers["Cache-Control"] = CACHE_HTTP
    a = _generar_y_procesar_matrices(operador, semana_a)
    b = _generar_y_procesar_matrices(operador, semana_b)
    if a.get("error"):
        return a
    if b.get("error"):
        return b

    # La suma exige dimensiones identicas. Como cada periodo puede traer distintos
    # clientes y distintos dias, ambas matrices se reindexan sobre la union de los ejes.
    clientes = sorted(set(a["nodos_de_control"]) | set(b["nodos_de_control"]))
    dias = [d for d in DIAS_ES if d in set(a["dias_semana"]) | set(b["dias_semana"])]

    def alinear(datos):
        fila_de = {c: i for i, c in enumerate(datos["nodos_de_control"])}
        col_de = {d: j for j, d in enumerate(datos["dias_semana"])}
        return [
            [datos["E"][fila_de[c]][col_de[d]] if c in fila_de and d in col_de else 0
             for d in dias]
            for c in clientes
        ]

    E1, E2 = alinear(a), alinear(b)
    suma = [[E1[i][j] + E2[i][j] for j in range(len(dias))] for i in range(len(clientes))]
    return {
        "descripcion": "Suma de matrices E1 + E2 (acumulado de dos periodos)",
        "periodos": [semana_a, semana_b],
        "nodos_de_control": clientes,
        "dias_semana": dias,
        "E1": E1,
        "E2": E2,
        "matriz_suma": suma,
        "frecuencia_por_nodo": [sum(f) for f in suma],
    }


@app.get("/analisis/comparativa-operadores", tags=["Analisis Principal"],
         summary="Capitulo 7: Comparacion entre operadores")
def get_comparativa(response: Response):
    """Ranking de cumplimiento y cobertura por operador.

    Responde al objetivo del documento de comparar el desempeno entre reponedores,
    que el analisis de un solo operador no permite ver.
    """
    response.headers["Cache-Control"] = CACHE_HTTP
    cache_key = "comparativa_operadores"
    if cache_key in cache:
        return cache[cache_key]

    query = f"""
        SELECT
            `Nombre reponedor` AS operador,
            COUNTIF(`Estado visita` != @fuera_de_plan)                       AS planificadas,
            COUNTIF(`Estado visita` IN UNNEST(@ejecutados))                  AS ejecutadas,
            COUNTIF(`Estado visita` = 'VISITADA')                            AS efectivas,
            COUNTIF(`Estado visita` = 'NO VISITADA')                         AS no_ejecutadas,
            COUNTIF(`Estado visita` = 'VISITADA PERO NO COMPLETADA')         AS incompletas,
            COUNTIF(`Estado visita` = @fuera_de_plan)                        AS fuera_de_plan,
            COUNT(DISTINCT `Cliente visitado`)                               AS nodos
        FROM `{TABLA}`
        WHERE `Nombre reponedor` IS NOT NULL
        GROUP BY operador
        ORDER BY operador
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("fuera_de_plan", "STRING", ESTADO_FUERA_DE_PLAN),
        bigquery.ArrayQueryParameter("ejecutados", "STRING", sorted(ESTADOS_EJECUTADOS)),
    ])

    try:
        filas = [dict(r) for r in client.query(query, job_config=job_config)]
    except Exception as exc:
        return {"error": "Error al consultar BigQuery", "detalle": str(exc)}

    resultado = []
    for f in filas:
        plan = f["planificadas"] or 0
        resultado.append({
            "operador": f["operador"],
            "nodos": f["nodos"],
            "planificadas": plan,
            "ejecutadas": f["ejecutadas"],
            "no_ejecutadas": f["no_ejecutadas"],
            "incompletas": f["incompletas"],
            "fuera_de_plan": f["fuera_de_plan"],
            "cumplimiento_pct": round(f["ejecutadas"] / plan * 100, 2) if plan else 0,
            "cumplimiento_efectivo_pct": round(f["efectivas"] / plan * 100, 2) if plan else 0,
        })
    resultado.sort(key=lambda x: x["cumplimiento_efectivo_pct"], reverse=True)
    respuesta = {
        "descripcion": "Comparativa de operadores del sistema de control",
        "total_operadores": len(resultado),
        "operadores": resultado,
    }
    cache[cache_key] = respuesta
    return respuesta


@app.get("/analisis/sistema-de-ecuaciones", tags=["Analisis Adicional"],
         summary="Capitulo 6: Sistema de Ecuaciones (Gauss-Jordan)")
def solve_sistema_ecuaciones(
    a: float = Query(1), b: float = Query(1), e: float = Query(20),
    c: float = Query(2), d: float = Query(1), f: float = Query(30),
):
    det = a * d - b * c
    pasos, solucion = [], {}
    pasos.append({"paso": "1. Matriz Ampliada", "matriz": [[a, b, e], [c, d, f]]})
    if det != 0 and a != 0:
        m1 = [[1, b / a, e / a], [c, d, f]]
        pasos.append({"paso": "2. F1 -> (1/a)*F1", "matriz": m1})
        f2 = m1[1][0]
        m2 = [m1[0], [0, m1[1][1] - f2 * m1[0][1], m1[1][2] - f2 * m1[0][2]]]
        pasos.append({"paso": "3. F2 -> F2 - (f)*F1", "matriz": m2})
        piv = m2[1][1]
        if piv != 0:
            m3 = [m2[0], [0, 1, m2[1][2] / piv]]
            pasos.append({"paso": "4. F2 -> (1/piv)*F2", "matriz": m3})
            f1 = m3[0][1]
            m4 = [[1, 0, m3[0][2] - f1 * m3[1][2]], m3[1]]
            pasos.append({"paso": "5. F1 -> F1 - (f)*F2", "matriz": m4})
            solucion = {"x": m4[0][2], "y": m4[1][2]}
    return {
        "descripcion": "Resolucion por Gauss-Jordan",
        "determinante_A": det,
        "pasos": pasos,
        "solucion": solucion,
    }


# --- Logica Interna: construccion de las matrices P y E ---

def _generar_y_procesar_matrices(reponedor: str, periodo: str = "TODO"):
    """Consulta BigQuery y arma las matrices del modelo. Cachea por operador y periodo.

    Filas    = puntos de venta (nodos de control).
    Columnas = dias de la semana, tomados de la columna `Dia visita` del origen.

    Se usa el dia de la semana y no la fecha calendario porque los registros con estado
    'NO VISITADA' llegan sin fecha (la visita nunca inicio) pero si conservan el dia
    planificado. Indexar por dia permite incluirlos sin inferir ni inventar ninguna
    fecha, y son justamente las celdas que producen la brecha D = P - E.
    """
    cache_key = f"matrices::{reponedor}::{periodo}"
    if cache_key in cache:
        return cache[cache_key]

    filtro_fecha = ""
    parametros = [bigquery.ScalarQueryParameter("reponedor", "STRING", reponedor)]
    if periodo and periodo != "TODO":
        try:
            inicio = date.fromisoformat(periodo)
        except ValueError:
            return {"error": "El periodo debe ser 'TODO' o una fecha AAAA-MM-DD."}
        # Al acotar a una semana, los registros sin fecha se conservan igual: pertenecen
        # a la ruta del operador y su dia planificado es conocido.
        filtro_fecha = """
          AND (`Fecha inicio` BETWEEN @inicio AND DATE_ADD(@inicio, INTERVAL 6 DAY)
               OR `Fecha inicio` IS NULL)
        """
        parametros.append(bigquery.ScalarQueryParameter("inicio", "DATE", inicio))

    query = f"""
        SELECT
            `Cliente visitado` AS cliente,
            `Dia visita`       AS dia_nombre,
            `Estado visita`    AS estado,
            COUNT(*)           AS veces
        FROM `{TABLA}`
        WHERE `Nombre reponedor` = @reponedor
          AND `Cliente visitado` IS NOT NULL
          AND `Dia visita` IS NOT NULL
          {filtro_fecha}
        GROUP BY cliente, dia_nombre, estado
    """
    job_config = bigquery.QueryJobConfig(query_parameters=parametros)

    try:
        registros = [dict(r) for r in client.query(query, job_config=job_config)]
    except Exception as exc:
        return {"error": "Error al consultar BigQuery", "detalle": str(exc)}

    if not registros:
        return {"error": "No se encontraron datos para el operador y periodo seleccionados."}

    # Ejes de la matriz. Las columnas siguen el orden natural de la semana, no el orden
    # en que aparecen los datos, y solo incluyen dias con actividad registrada.
    clientes = sorted({r["cliente"] for r in registros})
    dias_presentes = {(r["dia_nombre"] or "").strip().capitalize() for r in registros}
    dias = [d for d in DIAS_ES if d in dias_presentes]
    fila_de = {c: i for i, c in enumerate(clientes)}
    col_de = {d: j for j, d in enumerate(dias)}
    nF, nC = len(clientes), len(dias)

    # Conteos por celda: cuantas visitas se planificaron y cuantas se ejecutaron.
    p_cnt = [[0] * nC for _ in range(nF)]
    e_cnt = [[0] * nC for _ in range(nF)]
    ef_cnt = [[0] * nC for _ in range(nF)]
    fp_cnt = [[0] * nC for _ in range(nF)]
    E_estado = [["NO AGENDADO"] * nC for _ in range(nF)]

    for r in registros:
        dia = (r["dia_nombre"] or "").strip().capitalize()
        if dia not in col_de:
            continue
        i, j = fila_de[r["cliente"]], col_de[dia]
        estado = (r["estado"] or "").strip().upper()
        veces = int(r["veces"])

        if estado == ESTADO_FUERA_DE_PLAN:
            fp_cnt[i][j] += veces
        else:
            p_cnt[i][j] += veces
        if estado in ESTADOS_EJECUTADOS:
            e_cnt[i][j] += veces
        if estado == "VISITADA":
            ef_cnt[i][j] += veces

        # Se conserva el estado MAS DESFAVORABLE de la celda: si en alguna semana el
        # punto no fue atendido, la celda debe mostrarlo. Lo contrario ocultaria fallos.
        actual = E_estado[i][j]
        if actual == "NO AGENDADO" or PRIORIDAD_ESTADO.get(estado, 9) < PRIORIDAD_ESTADO.get(actual, 9):
            E_estado[i][j] = estado

    # Matrices binarias del documento: 1 = atencion realizada, 0 = no realizada.
    # P: la celda estaba planificada. E: se cumplieron TODAS las visitas planificadas
    # de esa celda. Asi D = P - E marca con 1 exactamente los puntos con incumplimiento.
    P = [[1 if p_cnt[i][j] > 0 else 0 for j in range(nC)] for i in range(nF)]
    E = [[1 if p_cnt[i][j] > 0 and e_cnt[i][j] >= p_cnt[i][j] else 0 for j in range(nC)] for i in range(nF)]
    E_efectiva = [[1 if p_cnt[i][j] > 0 and ef_cnt[i][j] >= p_cnt[i][j] else 0 for j in range(nC)] for i in range(nF)]

    # Indicadores del modelo, calculados en el servidor: unica fuente de verdad.
    planificadas = sum(sum(f) for f in p_cnt)
    ejecutadas = sum(sum(f) for f in e_cnt)
    efectivas = sum(sum(f) for f in ef_cnt)
    fuera_de_plan = sum(sum(f) for f in fp_cnt)
    incompletas = ejecutadas - efectivas
    celdas_planificadas = sum(sum(f) for f in P)
    celdas_con_fallo = sum(1 for i in range(nF) for j in range(nC) if P[i][j] - E[i][j] > 0)
    nodos_planificados = sum(1 for f in P if sum(f) > 0)
    nodos_atendidos = sum(1 for i in range(nF) if any(e_cnt[i][j] > 0 for j in range(nC)))

    respuesta = {
        "reponedor": reponedor,
        "periodo": periodo,
        "fecha_consultada": periodo,
        "nodos_de_control": clientes,
        "dias_semana": dias,
        "P": P,
        "E": E,
        "E_efectiva": E_efectiva,
        "E_estado": E_estado,
        # Conteos crudos: permiten leer la frecuencia real detras de la matriz binaria.
        "P_conteo": p_cnt,
        "E_conteo": e_cnt,
        "nota_metodologica": (
            "Las columnas son dias de la semana, no fechas calendario. En el origen, los "
            "registros 'NO VISITADA' no traen fecha (la visita nunca inicio) pero si el dia "
            "planificado; indexar por dia permite incluirlos sin inferir ninguna fecha. "
            "En la matriz binaria, E vale 1 solo si se cumplieron todas las visitas "
            "planificadas de esa celda."
        ),
        "indicadores": {
            "total_planificadas": planificadas,
            "total_ejecutadas": ejecutadas,
            "total_no_ejecutadas": planificadas - ejecutadas,
            "total_fuera_de_plan": fuera_de_plan,
            "total_incompletas": incompletas,
            "total_efectivas": efectivas,
            "celdas_planificadas": celdas_planificadas,
            "celdas_con_fallo": celdas_con_fallo,
            "nodos_planificados": nodos_planificados,
            "nodos_atendidos": nodos_atendidos,
            # Cumplimiento segun el documento: visitas realizadas / visitas planificadas.
            "indicador_cumplimiento_pct": round(ejecutadas / planificadas * 100, 2) if planificadas else 0,
            # Cumplimiento efectivo: solo las visitas que alcanzaron el minimo de minutos.
            "indicador_cumplimiento_efectivo_pct": round(efectivas / planificadas * 100, 2) if planificadas else 0,
            "indicador_cobertura_pct": round(nodos_atendidos / nodos_planificados * 100, 2) if nodos_planificados else 0,
        },
    }
    cache[cache_key] = respuesta
    return respuesta
