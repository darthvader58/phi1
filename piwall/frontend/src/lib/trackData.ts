/**
 * Accurate F1 circuit coordinate data and path utilities.
 * Each circuit is defined as control points traced from real track layouts.
 * Catmull-Rom spline interpolation creates smooth curves through these points.
 */

export interface TrackDef {
  name: string;
  displayName: string;
  points: [number, number][];
}

// ─── Circuit control points ──────────────────────────────────────────
// Coordinates in an 800×520 viewport, traced clockwise from start/finish.

const MONACO: [number, number][] = [
  // Start/Finish straight (heading east along the harbor front)
  [130, 330],
  [190, 335],
  // Sainte Devote (sharp right heading uphill/north)
  [250, 335], [275, 320], [275, 295],
  // Beau Rivage climb
  [280, 260], [285, 225], [295, 195],
  // Massenet curve (right, heading east toward Casino)
  [310, 170], [335, 150], [365, 140],
  // Casino Square area
  [400, 138], [425, 145],
  // Mirabeau Haute (right, heading south)
  [440, 160], [448, 180],
  // Mirabeau Basse
  [448, 205], [440, 225],
  // Loews / Fairmont Hairpin (tight U-turn back east)
  [425, 245], [408, 255], [408, 268], [425, 278], [445, 272],
  // Portier (heading east toward tunnel)
  [470, 262], [498, 255],
  // Tunnel entrance & through tunnel (heading southeast)
  [530, 252], [565, 255], [598, 268],
  // Tunnel exit
  [618, 288], [628, 310],
  // Nouvelle Chicane
  [628, 338], [618, 355], [625, 372],
  // Tabac (heading west along harbor)
  [615, 392], [590, 405], [555, 412],
  // Swimming Pool complex (chicane)
  [520, 415], [498, 402], [478, 412], [455, 420],
  // La Rascasse (tight right heading north-west)
  [420, 425], [388, 420], [358, 410],
  // Anthony Noghes (right heading east back to S/F)
  [320, 398], [280, 380], [240, 358],
  [195, 342], [155, 335],
];

const BAHRAIN: [number, number][] = [
  // Start/Finish straight (heading south on the left side)
  [195, 90],
  [195, 145],
  // Turn 1 (sharp right heading east)
  [195, 195], [210, 218], [235, 228],
  // Turn 2 (left, heading north-east)
  [265, 225], [280, 210],
  // Turn 3 (right, heading south-east)
  [295, 208], [310, 218],
  // Turn 4 (right hairpin heading south)
  [325, 238], [332, 260], [325, 282],
  // Turn 5 (left heading east)
  [315, 298], [310, 315], [318, 332],
  // Turn 6-7 (esses heading east)
  [338, 342], [358, 335], [378, 345],
  // Turn 8 (sharp left heading north)
  [395, 360], [402, 378], [395, 395],
  // Turn 9-10 (heading west then south)
  [378, 408], [358, 412], [342, 420],
  // Long back straight heading west
  [318, 432], [275, 440], [235, 440],
  // Turn 11 (left heading south)
  [205, 438], [185, 428],
  // Turn 12 (right heading west)
  [178, 410], [168, 392],
  // Turn 13 (left heading south)
  [162, 372], [158, 348],
  // Turn 14 (right, heading north back to straight)
  [155, 318], [158, 285], [165, 248],
  // Turn 15 (slight left)
  [172, 215], [178, 178],
  // Back to S/F straight heading north
  [182, 140], [188, 110],
];

const MONZA: [number, number][] = [
  // Start/Finish straight (heading north, right side of circuit)
  [420, 440],
  [415, 390], [410, 340],
  // Variante del Rettifilo (chicane at north end)
  [408, 295], [418, 268], [408, 248], [392, 238],
  // Short straight heading northwest
  [375, 228], [358, 222],
  // Curva Grande (long sweeping right heading east)
  [335, 218], [318, 210], [310, 195], [318, 178],
  [338, 165], [368, 152], [402, 142], [438, 138],
  // Variante della Roggia (chicane)
  [468, 142], [488, 152], [495, 168], [488, 185],
  // Lesmo 1 (right heading south)
  [482, 205], [478, 228],
  // Lesmo 2 (right heading south/southwest)
  [478, 252], [472, 275], [462, 298],
  // Straight heading south
  [455, 325], [452, 355],
  // Variante Ascari (chicane)
  [448, 378], [435, 395], [428, 408], [438, 422],
  // Curva Parabolica (long sweeping right heading west then north)
  [445, 438], [440, 455], [428, 468],
  [408, 475], [385, 478], [362, 472],
  [345, 462], [338, 448],
  // Pit straight heading north back to S/F
  [345, 432], [358, 418],
  [378, 440], [400, 445],
];

const SPA: [number, number][] = [
  // Start/Finish (heading east, at the north end)
  [115, 118],
  [155, 118],
  // La Source hairpin (sharp right, then heading south/downhill)
  [185, 118], [200, 128], [205, 145], [195, 158], [178, 162],
  // Drop down to Eau Rouge (heading south-southeast)
  [168, 178], [162, 205], [165, 232],
  // Eau Rouge & Raidillon (famous uphill left-right, heading east)
  [172, 255], [188, 268], [212, 262], [238, 245],
  // Kemmel Straight (heading east/northeast)
  [278, 228], [328, 208], [385, 192], [445, 178],
  // Les Combes (chicane, heading south)
  [488, 172], [518, 175], [535, 188], [532, 208],
  // Malmedy (heading south)
  [525, 228], [522, 252],
  // Rivage hairpin (right, heading south-west)
  [518, 275], [508, 295], [492, 308],
  // Downhill through forest
  [475, 322], [455, 340], [435, 358],
  // Pouhon (double-apex left, heading southwest)
  [412, 378], [388, 395], [362, 405], [338, 398],
  // Fagnes / Campuget
  [318, 392], [298, 398], [278, 408],
  // Stavelot (right-left heading west)
  [255, 418], [232, 422], [212, 415], [198, 402],
  // Blanchimont (fast left heading northwest)
  [185, 382], [172, 355], [158, 322],
  // Approach to Bus Stop
  [148, 288], [138, 255],
  // Bus Stop chicane
  [132, 225], [125, 198], [128, 175],
  // Pit straight back to S/F heading north
  [125, 152], [118, 135],
];

const SILVERSTONE: [number, number][] = [
  // Start/Finish (heading south, then into Copse)
  [258, 248],
  [268, 275],
  // Copse (fast right)
  [278, 302], [295, 318], [318, 325],
  // Maggots (fast left)
  [345, 322], [365, 312],
  // Becketts (fast right)
  [385, 298], [402, 285],
  // Chapel (left onto Hangar Straight)
  [418, 275], [438, 268],
  // Hangar Straight (heading east)
  [468, 262], [508, 255], [548, 248],
  // Stowe (right heading south)
  [575, 248], [595, 258], [605, 278],
  // Vale (left heading south-west)
  [605, 302], [598, 322], [585, 338],
  // Club (right heading west)
  [568, 352], [548, 358], [525, 355],
  // Hamilton Straight (heading west)
  [498, 348], [465, 345], [432, 348],
  // Village (right heading northwest)
  [408, 352], [388, 358], [372, 368],
  // The Loop (left heading west)
  [358, 378], [342, 388], [322, 385],
  // Aintree (heading west)
  [302, 378], [278, 375],
  // Brooklands (right heading north)
  [258, 372], [242, 365], [232, 348],
  // Luffield (left heading northwest)
  [228, 328], [222, 308], [218, 288],
  // Woodcote (right heading northeast)
  [218, 268], [222, 252], [235, 242],
  // Back to Wellington Straight / S/F
  [248, 240],
];

const SUZUKA: [number, number][] = [
  // Start/Finish (heading west along the south side)
  [538, 432],
  [488, 438], [438, 442],
  // Turn 1 (right heading north)
  [388, 440], [355, 428], [335, 405],
  // Turn 2 (left heading north)
  [325, 378], [328, 348], [338, 322],
  // S-Curves (heading east through the northern section)
  [352, 298], [368, 282],
  [385, 275], [398, 285],
  [412, 278], [428, 268],
  [445, 275], [455, 288],
  // Dunlop curve (right heading south-east)
  [468, 302], [478, 322],
  // Degner 1 (right heading south)
  [482, 348], [480, 372],
  // Degner 2 (right heading south-west)
  [472, 392], [455, 405],
  // ─── CROSSOVER ZONE ─── (the figure-8 crosses here)
  // Path heads south-west, crossing UNDER the back straight
  [435, 415], [412, 418],
  // Hairpin (sharp left heading east)
  [388, 425], [372, 438], [358, 448],
  [342, 448], [328, 438],
  // Spoon curve (heading north/northeast)
  [318, 418], [308, 395],
  [302, 368], [298, 342],
  [298, 318], [305, 298],
  // 200R & Back straight (heading east)
  // This passes OVER the Degner→Hairpin section (the crossover bridge)
  [318, 282], [342, 275],
  [375, 278], [412, 288],
  [448, 305], [478, 325],
  // 130R (fast left heading south-east)
  [505, 345], [525, 368],
  // Casio Triangle chicane
  [538, 392], [548, 408], [545, 425],
  // Back to S/F
  [540, 435],
];

// ─── Track registry ────────────────────────────────────────────────

export const TRACK_DEFS: Record<string, TrackDef> = {
  monaco:      { name: "monaco",      displayName: "Monaco",       points: MONACO },
  bahrain:     { name: "bahrain",     displayName: "Bahrain",      points: BAHRAIN },
  monza:       { name: "monza",       displayName: "Monza",        points: MONZA },
  spa:         { name: "spa",         displayName: "Spa",          points: SPA },
  silverstone: { name: "silverstone", displayName: "Silverstone",  points: SILVERSTONE },
  suzuka:      { name: "suzuka",      displayName: "Suzuka",       points: SUZUKA },
};

// ─── Catmull-Rom spline utilities ──────────────────────────────────

/**
 * Evaluate a Catmull-Rom spline segment between p1 and p2
 * with neighbouring points p0 and p3 at parameter t ∈ [0, 1].
 */
function catmullRom(
  p0: [number, number],
  p1: [number, number],
  p2: [number, number],
  p3: [number, number],
  t: number,
): [number, number] {
  const t2 = t * t;
  const t3 = t2 * t;
  const x =
    0.5 *
    (2 * p1[0] +
      (-p0[0] + p2[0]) * t +
      (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
      (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3);
  const y =
    0.5 *
    (2 * p1[1] +
      (-p0[1] + p2[1]) * t +
      (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
      (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3);
  return [x, y];
}

/** Pre-computed dense polyline + cumulative arc lengths for a track. */
export interface TrackPath {
  /** Dense array of interpolated points. */
  points: [number, number][];
  /** Cumulative arc length at each point (first entry = 0). */
  arcLengths: number[];
  /** Total path length. */
  totalLength: number;
}

const SAMPLES_PER_SEGMENT = 32;

/** Build a dense polyline from control points using Catmull-Rom spline. */
export function buildTrackPath(controlPoints: [number, number][]): TrackPath {
  const n = controlPoints.length;
  const pts: [number, number][] = [];

  for (let i = 0; i < n; i++) {
    const p0 = controlPoints[(i - 1 + n) % n];
    const p1 = controlPoints[i];
    const p2 = controlPoints[(i + 1) % n];
    const p3 = controlPoints[(i + 2) % n];

    for (let s = 0; s < SAMPLES_PER_SEGMENT; s++) {
      pts.push(catmullRom(p0, p1, p2, p3, s / SAMPLES_PER_SEGMENT));
    }
  }

  // Compute arc lengths
  const arcLengths: number[] = [0];
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i][0] - pts[i - 1][0];
    const dy = pts[i][1] - pts[i - 1][1];
    total += Math.sqrt(dx * dx + dy * dy);
    arcLengths.push(total);
  }

  return { points: pts, arcLengths, totalLength: total };
}

/**
 * Get a point + tangent direction on the track at fraction t ∈ [0, 1).
 * Uses binary search on cumulative arc lengths for O(log n).
 */
export function getPointAtFraction(
  path: TrackPath,
  t: number,
): { x: number; y: number; angle: number } {
  const frac = ((t % 1) + 1) % 1; // wrap to [0, 1)
  const targetLen = frac * path.totalLength;

  // Binary search
  let lo = 0;
  let hi = path.arcLengths.length - 1;
  while (lo < hi - 1) {
    const mid = (lo + hi) >> 1;
    if (path.arcLengths[mid] <= targetLen) lo = mid;
    else hi = mid;
  }

  // Interpolate between lo and hi
  const segLen = path.arcLengths[hi] - path.arcLengths[lo];
  const segFrac = segLen > 0 ? (targetLen - path.arcLengths[lo]) / segLen : 0;

  const p0 = path.points[lo];
  const p1 = path.points[hi % path.points.length];

  const x = p0[0] + (p1[0] - p0[0]) * segFrac;
  const y = p0[1] + (p1[1] - p0[1]) * segFrac;

  // Tangent angle
  const dx = p1[0] - p0[0];
  const dy = p1[1] - p0[1];
  const angle = Math.atan2(dy, dx);

  return { x, y, angle };
}
