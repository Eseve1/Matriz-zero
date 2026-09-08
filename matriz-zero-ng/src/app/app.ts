import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { CommonModule, NgClass } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';

// --- Interfaces para tipado de datos de la API ---
interface Reponedor {
  nombre: string;
}

interface Indicadores {
  total_planificadas: number;
  total_ejecutadas: number;
  total_no_ejecutadas: number;
  total_incompletas: number;
  total_fuera_de_plan: number;
  celdas_con_fallo: number;
  puntos_planificados: number;
  puntos_atendidos: number;
  indicador_cumplimiento_pct: number;
  indicador_cumplimiento_efectivo_pct: number;
  indicador_cobertura_pct: number;
}

interface ResumenDatos {
  registros: number; reponedores: number; puntos_de_venta: number; rutas: number;
  desde: string; hasta: string;
  planificadas: number; ejecutadas: number; completas: number;
  incompletas: number; no_ejecutadas: number; fuera_de_plan: number;
  cumplimiento_pct: number; cumplimiento_efectivo_pct: number;
}

interface ReponedorComparado {
  reponedor: string;
  puntos_de_venta: number;
  planificadas: number;
  ejecutadas: number;
  no_ejecutadas: number;
  incompletas: number;
  cumplimiento_pct: number;
  cumplimiento_efectivo_pct: number;
}

interface MatrizResponse {
  reponedor: string;
  fecha_consultada: string;
  puntos_de_venta: string[];
  dias_semana: string[];
  P: number[][];
  E: number[][];
  E_estado: string[][];
  P_conteo: number[][];
  E_conteo: number[][];
  indicadores: Indicadores;
  nota_metodologica: string;
  error?: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule, NgClass],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App implements OnInit {
  private readonly http = inject(HttpClient);
  // Angular 21 corre sin zone.js: las asignaciones hechas dentro de un subscribe no
  // disparan deteccion de cambios por si solas. Hay que pedirla explicitamente o la
  // pantalla se queda con los valores iniciales aunque los datos ya hayan llegado.
  private readonly cdr = inject(ChangeDetectorRef);
  readonly apiUrl = 'https://matriz-zero-api-871430712754.us-central1.run.app';

  // --- Estado de la UI ---
  loading = false;        // Carga inicial (pantalla vacia)
  actualizando = false;   // Recalculo con datos ya en pantalla
  error: string | null = null;

  // --- Cache local del navegador: evita repetir la llamada a la API ---
  private readonly cacheLocal = new Map<string, MatrizResponse>();
  
  // --- Filtros Principales ---
  reponedores: Reponedor[] = [];
  reponedorSeleccionado: string = '';
  fechaSeleccionada: string = 'TODO'; // Todo el periodo disponible
  readonly SEMANAS = [
    { valor: 'TODO', etiqueta: 'Todo el período (Agosto 2026)' },
    { valor: '2026-08-01', etiqueta: 'Semana 1 (Sáb 01-Ago)' },
    { valor: '2026-08-08', etiqueta: 'Semana 2 (Sáb 08-Ago)' },
    { valor: '2026-08-15', etiqueta: 'Semana 3 (Sáb 15-Ago)' },
    { valor: '2026-08-22', etiqueta: 'Semana 4 (Sáb 22-Ago)' },
  ];

  // --- Datos del Modelo Matricial ---
  P: number[][] = [];
  E: number[][] = [];
  E_estado: string[][] = [];
  D: number[][] = [];
  PDV: string[] = []; // Puntos de venta (filas de la matriz)
  DIAS: string[] = [];
  nF = 0; nC = 0; // Dimensiones

  // --- Resultados del Análisis ---
  // Vectores de E (ejecución) y de P (planificación): suma por fila y por columna.
  // Se muestran como columna y fila Σ en el borde de cada matriz.
  vf: number[] = []; vc: number[] = [];
  vfP: number[] = []; vcP: number[] = [];
  totalE = 0; totalP = 0;
  totalPlanificadas = 0; totalEjecutadas = 0;
  totalPdvPlanificados = 0; totalPdvAtendidos = 0;
  cump = 0; cob = 0; perdidas = 0;
  cumpEfectivo = 0; incompletas = 0; fueraDePlan = 0;
  notaMetodologica = '';
  
  // --- Parámetros de Transformaciones ---
  k = 2; // Escalar para ponderación
  kE: number[][] = []; // Matriz ponderada k*E

  // --- Ficha del conjunto de datos ---
  // Cifras globales del origen: dimensionan el alcance sin tener que recordarlas.
  resumen: ResumenDatos | null = null;

  // --- Comparativa entre reponedores ---
  comparativa: ReponedorComparado[] = [];
  cargandoComparativa = false;
  
  // --- Parámetros Sistema de Ecuaciones ---
  ca = 1; cb = 1; ce = 20;
  cc = 2; cd = 1; cf = 30;
  det = 0;
  pasoGauss = 0;
  pasosGauss: any[] = [];
  
  ngOnInit() {
    // Se inicializa primero porque es matematica pura: debe verse aunque la API falle.
    this.construirGauss();
    this.cargarReponedores();
    this.cargarComparativa();
    this.cargarResumen();
  }

  /** Ficha global del origen de datos. */
  cargarResumen() {
    this.http.get<ResumenDatos>(`${this.apiUrl}/datos/resumen`).subscribe({
      next: (d) => { this.resumen = d; this.cdr.markForCheck(); },
      error: () => { /* la ficha es informativa: si falla, la pantalla sigue */ }
    });
  }

  /** Ranking de reponedores: responde al objetivo de compararlos entre sí (§3.1 y §6). */
  cargarComparativa() {
    this.cargandoComparativa = true;
    this.http.get<{ reponedores: ReponedorComparado[] }>(`${this.apiUrl}/analisis/comparativa-reponedores`)
      .subscribe({
        next: (data) => {
          this.comparativa = data.reponedores || [];
          this.cargandoComparativa = false;
          this.cdr.markForCheck();
        },
        error: () => { this.cargandoComparativa = false; this.cdr.markForCheck(); }
      });
  }

  cargarReponedores() {
    this.loading = true;
    this.http.get<Reponedor[]>(`${this.apiUrl}/sistema/reponedores`).subscribe({
      next: (data) => {
        this.reponedores = data;
        const destacado = data.find(r => r.nombre.includes('SERGIO.OVANDO'));
        this.reponedorSeleccionado = destacado ? destacado.nombre : (data[0]?.nombre || '');
        if (this.reponedorSeleccionado) {
          this.cargarMatriz();
        } else {
          this.loading = false;
        }
        this.cdr.markForCheck();
      },
      error: (err) => this.handleError(err, 'No se pudo conectar con la API de Cloud Run.')
    });
  }

  cargarMatriz(forzar = false) {
    if (!this.reponedorSeleccionado) return;
    const clave = this.claveCache(this.reponedorSeleccionado, this.fechaSeleccionada);

    // Si ya consultamos esta combinacion antes, se pinta al instante y sin red.
    if (!forzar && this.cacheLocal.has(clave)) {
      this.aplicarDatos(this.cacheLocal.get(clave)!);
      this.precargarSemanas();
      return;
    }

    if (forzar) this.cacheLocal.delete(clave);
    // Solo bloqueamos la pantalla la primera vez; despues es un refresco suave.
    if (this.P.length) { this.actualizando = true; } else { this.loading = true; }
    this.error = null;

    this.http.get<MatrizResponse>(this.urlMatriz(this.reponedorSeleccionado, this.fechaSeleccionada)).subscribe({
      next: (data) => {
        this.loading = false;
        this.actualizando = false;
        if (data.error) {
          this.error = data.error;
          this.limpiarDatos();
          this.cdr.markForCheck();
          return;
        }
        this.cacheLocal.set(clave, data);
        this.aplicarDatos(data);
        this.precargarSemanas();
        this.cdr.markForCheck();
      },
      error: (err) => this.handleError(err, 'Error de comunicación al procesar la consulta matricial.')
    });
  }

  /** Vuelca una respuesta (de red o de cache) al estado del modelo. */
  private aplicarDatos(data: MatrizResponse) {
    this.error = null;
    this.P = data.P;
    this.E = data.E;
    this.E_estado = data.E_estado;
    this.PDV = data.puntos_de_venta;
    this.DIAS = data.dias_semana;
    this.nF = this.P.length;
    this.nC = this.P[0]?.length || 0;
    this.notaMetodologica = data.nota_metodologica || '';

    // Los indicadores llegan calculados del backend: una sola fuente de verdad.
    // El navegador solo reconstruye D = P - E y los vectores, que son la parte
    // del modelo que conviene mostrar operando en vivo.
    const ind = data.indicadores;
    if (ind) {
      this.totalPlanificadas = ind.total_planificadas;
      this.totalEjecutadas = ind.total_ejecutadas;
      this.totalPdvPlanificados = ind.puntos_planificados;
      this.totalPdvAtendidos = ind.puntos_atendidos;
      this.cump = ind.indicador_cumplimiento_pct;
      this.cumpEfectivo = ind.indicador_cumplimiento_efectivo_pct;
      this.cob = ind.indicador_cobertura_pct;
      this.incompletas = ind.total_incompletas;
      this.fueraDePlan = ind.total_fuera_de_plan;
    }
    this.recalcularTodo();
  }

  /** Trae en segundo plano las demas semanas del operador para que el cambio sea instantaneo. */
  private precargarSemanas() {
    for (const semana of this.SEMANAS) {
      const clave = this.claveCache(this.reponedorSeleccionado, semana.valor);
      if (this.cacheLocal.has(clave)) continue;
      this.cacheLocal.set(clave, null as any); // reserva el lugar: evita pedirla dos veces
      this.http.get<MatrizResponse>(this.urlMatriz(this.reponedorSeleccionado, semana.valor)).subscribe({
        next: (data) => { if (data && !data.error) { this.cacheLocal.set(clave, data); } else { this.cacheLocal.delete(clave); } },
        error: () => this.cacheLocal.delete(clave)
      });
    }
  }

  private claveCache = (operador: string, fecha: string) => `${operador}|${fecha}`;

  urlMatriz = (reponedor: string, fecha: string) =>
    `${this.apiUrl}/analisis/matrices-de-estado?reponedor=${encodeURIComponent(reponedor)}&fecha_semana=${fecha}`;

  recalcularTodo() {
    if (!this.P.length || !this.E.length) return;

    // 1. Brecha D = P - E, operada en vivo sobre las matrices que muestra la pantalla.
    this.D = this.P.map((fila, i) => fila.map((v, j) => v - this.E[i][j]));
    // Solo los valores positivos son incumplimientos. Los negativos (visitas fuera de
    // plan) no deben restar de los fallos: se informan por separado.
    this.perdidas = this.D.reduce((sum, fila) => sum + fila.filter(v => v > 0).length, 0);

    // 2. Vectores: por fila = frecuencia por punto de venta, por columna = por día.
    const sumaFilas = (m: number[][]) => m.map(f => f.reduce((a, b) => a + b, 0));
    const sumaCols = (m: number[][]) => this.DIAS.map((_, j) => m.reduce((s, f) => s + (f[j] || 0), 0));
    this.vf = sumaFilas(this.E);
    this.vc = sumaCols(this.E);
    this.vfP = sumaFilas(this.P);
    this.vcP = sumaCols(this.P);
    this.totalE = this.vf.reduce((a, b) => a + b, 0);
    this.totalP = this.vfP.reduce((a, b) => a + b, 0);

    // 3. Transformación lineal: ponderación por escalar k*E
    this.kE = this.E.map(fila => fila.map(v => +(v * this.k).toFixed(2)));

    // 4. Sistema de Ecuaciones
    this.construirGauss();

    // 4. Renderizado de Fórmulas LaTeX
    setTimeout(() => this.renderLaTex(), 0);
  }

  construirGauss() {
    this.det = this.ca * this.cd - this.cb * this.cc;
    const { ca: a, cb: b, ce: e, cc: c, cd: d, cf: f } = this;
    this.pasosGauss = [{ op: 'Matriz ampliada del sistema', m: [[a, b, e], [c, d, f]] }];
    if (this.det !== 0 && a !== 0) {
      let m = [[1, b / a, e / a], [c, d, f]];
      this.pasosGauss.push({ op: `F_1 \\to \\frac{1}{${a}}F_1`, m: m.map(r => r.slice()) });
      const factorF2 = m[1][0];
      m = [m[0], [0, m[1][1] - factorF2 * m[0][1], m[1][2] - factorF2 * m[0][2]]];
      this.pasosGauss.push({ op: `F_2 \\to F_2 - (${this.nfmt(factorF2)})F_1`, m: m.map(r => r.slice()) });
      const piv = m[1][1];
      if (piv !== 0) {
        m = [m[0], [0, 1, m[1][2] / piv]];
        this.pasosGauss.push({ op: `F_2 \\to \\frac{1}{${this.nfmt(piv)}}F_2`, m: m.map(r => r.slice()) });
        const factorF1 = m[0][1];
        m = [[1, 0, m[0][2] - factorF1 * m[1][2]], m[1]];
        this.pasosGauss.push({
          op: `F_1 \\to F_1 - (${this.nfmt(factorF1)})F_2`, m: m.map(r => r.slice()),
          sol: `x = ${this.nfmt(m[0][2])},  y = ${this.nfmt(m[1][2])}`
        });
      }
    }
    if (this.pasoGauss >= this.pasosGauss.length) this.pasoGauss = this.pasosGauss.length - 1;
    setTimeout(() => this.renderGaussLaTex(), 0);
  }
  
  // --- Métodos de UI y Helpers ---
  getClaseEstado(estado: string): string {
    if (estado.startsWith('VISITADA PERO')) return 'estado-incompleta';
    if (estado === 'VISITADA') return 'estado-visitada';
    if (estado === 'NO VISITADA') return 'estado-no-visitada';
    if (estado === 'VISITADA NO PLANIFICADA') return 'estado-no-planificada';
    return 'estado-no-agendado';
  }

  private handleError(error: any, defaultMessage: string) {
    console.error(error);
    this.error = defaultMessage;
    this.loading = false;
    this.actualizando = false;
    this.cdr.markForCheck();
  }

  private limpiarDatos() {
    this.P = []; this.E = []; this.E_estado = []; this.D = []; this.PDV = [];
    this.nF = 0; this.nC = 0;
    this.vf = []; this.vc = []; this.vfP = []; this.vcP = [];
    this.totalE = 0; this.totalP = 0; this.kE = [];
  }
  
  reiniciarGauss() { this.pasoGauss = 0; this.construirGauss(); }
  avanzarGauss() { if (this.pasoGauss < this.pasosGauss.length - 1) { this.pasoGauss++; setTimeout(() => this.renderGaussLaTex(), 0); }}
  
  nfmt = (v: number) => Number.isInteger(v) ? String(v) : v.toFixed(2);
  mtx = (m: number[][]) => '\\begin{bmatrix}' + m.map(f => f.map(v => this.nfmt(v)).join(' & ')).join(' \\\\ ') + '\\end{bmatrix}';

  restablecerValores() {
    this.ca=1; this.cb=1; this.ce=20; this.cc=2; this.cd=1; this.cf=30;
    this.k = 2; this.pasoGauss = 0;
    this.cacheLocal.clear();
    this.cargarMatriz(true); // Restablecer si consulta de nuevo a la API
  }

  // --- Renderizado de Fórmulas con KaTeX ---
  private renderLaTex() {
    const katex = (window as any).katex;
    if (!katex) return;
    const render = (id: string, formula: string) => {
      const el = document.getElementById(id);
      if (el) try { katex.render(formula, el, { throwOnError: false, displayMode: true }); } catch (e) { el.textContent = formula; }
    };
    render('texResta', `D = P - E = ${this.mtx(this.P)} - ${this.mtx(this.E)} = ${this.mtx(this.D)}`);
    render('formEscalar', `k E \\quad \\text{con} \\quad k = ${this.k}, \\quad \\sum k E = ${(this.totalEjecutadas * this.k).toFixed(1)}`);
  }

  private renderGaussLaTex() {
    const katex = (window as any).katex;
    if (!katex) return;
    const render = (id: string, formula: string, displayMode: boolean) => {
      const el = document.getElementById(id);
      if (el) try { katex.render(formula, el, { throwOnError: false, displayMode }); } catch (e) { el.textContent = formula; }
    };
    render('texDet', `\\det A = ad - bc = ${this.nfmt(this.det)}`, true);
    this.pasosGauss.forEach((p, idx) => {
      render(`gauss-op-${idx}`, p.op, false);
      render(`gauss-mat-${idx}`, `\\left[\\begin{array}{cc|c} ${p.m.map((row: number[]) => row.map(v => this.nfmt(v)).join(' & ')).join(' \\\\ ')} \\end{array}\\right]`, true);
    });
  }
}
