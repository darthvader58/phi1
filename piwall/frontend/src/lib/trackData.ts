/**
 * Accurate F1 circuit coordinate data and path utilities.
 * Each circuit is defined as control points traced from official F1 track maps.
 * Catmull-Rom spline interpolation creates smooth curves through these points.
 */

export interface TrackDef {
  name: string;
  displayName: string;
  points: [number, number][];
}

// ─── Circuit control points ──────────────────────────────────────────
// Coordinates in an 800×520 viewport, traced from official F1 circuit maps.
// Each track is traced in the direction cars drive, starting from S/F.

// ═══════════════════════════════════════════════════════════════════════
// MONACO — Circuit de Monaco (Monte Carlo)
// Shape: S/F at upper-left going up-right. Sector 1 (red) extends right
// to a tight complex (turns 04-08). Comes back left through the center.
// Sector 3 descends down the left side. Rascasse at bottom-left.
// ═══════════════════════════════════════════════════════════════════════
const MONACO: [number, number][] = [
  // S/F — pit straight heading up-right (left side of circuit)
  [130, 185], [165, 162], [205, 135], [255, 105], [310, 78],
  // Turn 01 — Sainte Devote (top of circuit, sharp right heading right-down)
  [355, 60], [390, 55], [418, 62], [435, 80],
  // Descending from Sainte Devote into sector 1
  [448, 108], [455, 140], [462, 170],
  // Turn 02 — heading right
  [472, 200], [480, 218],
  // Turn 03 — continuing right and slightly down
  [492, 242], [505, 255],
  // Heading right to the sector 2 complex
  [525, 248], [548, 230], [575, 205],
  // Turn 04 — Mirabeau area
  [600, 185], [622, 170],
  // Turn 05 — heading further right
  [645, 160], [660, 155],
  // Turns 06-07 — tight complex far right
  [675, 168], [682, 185], [690, 202],
  [700, 218],
  // Turn 08 — rightmost point
  [718, 232], [722, 252],
  // Heading back left from the far-right complex
  [718, 272], [705, 292],
  // Turn 09 — heading back left
  [685, 315], [660, 332],
  // Heading back left across the circuit (sector 2 → center)
  [628, 342], [595, 338],
  // Turn 10 — center area
  [548, 310], [525, 298],
  // Turn 11 — heading left
  [500, 288], [478, 280],
  // Heading up-left toward turn 12
  [450, 258], [428, 238],
  // Turn 12 — left side, heading down
  [398, 210], [380, 200],
  // Turn 13 — heading down-left
  [355, 218], [335, 235],
  // Turn 14
  [318, 258], [308, 275],
  // Sector 3 — descending down the left side
  [298, 296], [290, 315],
  // Turn 15-16 — continuing down
  [282, 335], [275, 352],
  [268, 370],
  // Turn 17 — Rascasse (bottom-left)
  [248, 388], [235, 400],
  // Turn 18 — bottom
  [238, 418], [248, 432],
  // Turn 19 — heading back up-left
  [238, 435], [218, 420],
  // Path curves back up along the left edge to S/F
  [195, 398], [175, 365],
  [158, 325], [145, 280],
  [138, 240], [134, 210],
];

// ═══════════════════════════════════════════════════════════════════════
// MONZA — Autodromo Nazionale Monza
// Shape: S/F straight at the bottom-right running left. Goes UP through
// sector 1 (two chicanes) to the top-left. Curves right at top (Curva
// Grande). Lesmos descend right side. Back straight goes FAR RIGHT to
// Turn 11 (Parabolica). Returns left to S/F.
// ═══════════════════════════════════════════════════════════════════════
const MONZA: [number, number][] = [
  // S/F — bottom of circuit, heading LEFT
  [700, 452],
  [640, 448], [570, 446],
  // Turn 01 — Variante del Rettifilo chicane (bottom-center-left)
  [498, 445], [468, 442], [448, 430],
  // Turn 02 — just above turn 01
  [438, 408], [445, 392],
  // Turn 03 — to the left heading up
  [435, 375], [415, 362],
  // Heading up-left through sector 1
  [390, 348], [362, 328],
  [330, 300], [298, 268],
  // Approaching turns 04-05 (top-left area)
  [268, 238], [245, 208],
  // Turn 04 — upper-left, heading right
  [230, 180], [222, 155],
  // Turn 05 — top-left
  [218, 132], [222, 112],
  // Curving right at the top — Curva Grande
  [232, 95], [252, 80], [278, 72],
  // Turn 06 — heading right along the top
  [310, 68], [342, 70],
  // Turn 07 — continuing right
  [375, 78], [400, 90],
  // Descending through Lesmos (sector 2, heading down-right)
  [418, 108], [428, 132],
  // Turn 08 — Lesmo 1
  [435, 158], [438, 182],
  // Turn 09 — heading down and right
  [445, 208], [455, 238],
  // Continuing descent
  [468, 268], [482, 295],
  // Turn 10 — transition to back straight (heading right)
  [498, 318], [518, 335],
  // Long back straight heading FAR RIGHT (sector 3)
  [548, 348], [590, 360], [640, 372],
  [690, 382], [728, 390],
  // Turn 11 — Parabolica (far right, sweeping right curve heading back left)
  [752, 398], [765, 412], [768, 432],
  [762, 450], [748, 462],
  [728, 470], [705, 472],
  // Heading back left to S/F
  [680, 468], [660, 462],
  // Rejoining S/F straight
  [720, 455],
];

// ═══════════════════════════════════════════════════════════════════════
// BAHRAIN — Bahrain International Circuit (Sakhir)
// Shape: S/F straight at the bottom running left-to-right. Left section
// goes UP to turn 04 at the very top. Complex infield (turns 05-10) in
// the center. Right section extends far right to a triangular area
// (turns 11-15). Long straight back at bottom connects to S/F.
// ═══════════════════════════════════════════════════════════════════════
const BAHRAIN: [number, number][] = [
  // S/F — bottom, heading LEFT from right side
  [720, 468],
  [660, 465], [590, 462], [520, 460],
  // Turn 01 — bottom-left, braking from main straight
  [448, 458], [398, 455], [358, 448],
  // Heading up after turn 01
  [338, 435], [328, 412],
  // Turn 02 — heading up
  [332, 390], [340, 372],
  // Turn 03 — left of turn 02
  [330, 355], [312, 342],
  // Heading UP along the left side (sector 1)
  [298, 320], [285, 290],
  [275, 258], [268, 225],
  [265, 190], [268, 155],
  // Turn 04 — very top of circuit
  [278, 118], [298, 78], [325, 52], [355, 42],
  // Heading down-right to turns 05-06
  [375, 48], [388, 62],
  // Turn 05 — below turn 04
  [398, 88], [410, 108],
  // Turn 06 — below turn 05
  [415, 128], [412, 148],
  // Turn 07 — heading down
  [408, 172], [415, 195],
  // Complex infield section (turns 08-10)
  // Turn 08
  [428, 218], [445, 232],
  // Turn 09
  [462, 240], [478, 248],
  // Turn 10 — heading right, then down
  [490, 258], [492, 278], [482, 298],
  // Heading into sector 3
  [472, 312], [462, 328],
  // Long section heading RIGHT
  [472, 348], [498, 362],
  // Turn 11 — right of center
  [528, 372], [555, 378],
  // Heading up toward turns 12-13
  [572, 368], [582, 348],
  [588, 320], [590, 288],
  // Turn 12 — upper right area
  [588, 255], [582, 225],
  [575, 195], [570, 165],
  // Turn 13 — top right (topmost point of right section)
  [565, 130], [558, 98], [555, 72],
  // Heading right and down from turn 13
  [568, 65], [595, 72],
  [628, 95], [660, 135],
  // Turn 14 — far right
  [690, 182], [712, 235],
  [722, 290], [728, 345],
  // Turn 15 — bottom-right, heading left along the bottom
  [730, 398], [728, 435], [722, 458],
  // Back to main straight heading left
  [720, 468],
];

// ═══════════════════════════════════════════════════════════════════════
// SPA — Circuit de Spa-Francorchamps
// Shape: S/F at bottom-center-left. La Source (turn 01) is a hairpin
// just left of S/F. Drops to Eau Rouge (turn 02), then climbs UP-RIGHT
// along the Kemmel Straight. Turn 05 at the very top. Les Combes
// (turns 06-07) at top-right. Descends right side. Large extension to
// the far left (Pouhon/Stavelot at turns 13-14). Returns right to S/F.
// ═══════════════════════════════════════════════════════════════════════
const SPA: [number, number][] = [
  // S/F — bottom area, heading left toward La Source
  [380, 435],
  [350, 428], [322, 418],
  // Turn 01 — La Source hairpin (sharp right U-turn)
  [295, 408], [278, 395], [272, 378],
  [278, 362], [292, 355], [308, 358],
  // Heading slightly down to Eau Rouge (turn 02)
  [318, 365], [322, 378], [328, 392],
  // Turn 02 — Eau Rouge dip, then Raidillon heading UP-RIGHT
  [338, 402], [348, 395], [358, 378],
  // Raidillon — steep uphill heading up-right
  [372, 355], [388, 335],
  // Turn 03-04 — continuing up-right
  [408, 312], [425, 292],
  // Kemmel Straight — long straight heading UP-RIGHT to the top
  [448, 268], [478, 242], [518, 215],
  [558, 188], [598, 162],
  // Turn 05 — at the very top of the circuit
  [638, 132], [665, 105], [685, 78], [698, 58],
  // Turns 06-07 — Les Combes, heading right then down (top-right area)
  [715, 62], [732, 78], [742, 98],
  [745, 122],
  // Turn 08 — far right, heading down
  [748, 148], [745, 175],
  // Turn 09 — continuing down
  [738, 202], [725, 228],
  // Turn 10 — heading left and down (right side descent)
  [708, 252], [690, 272],
  // Turn 11 — continuing descent
  [672, 295], [652, 318],
  // Turn 12 — heading further left and down
  [628, 340], [600, 358],
  // Heading far left through the lower portion
  [572, 372], [540, 385],
  // Turns 13-14 — Pouhon/Stavelot area (far left extension)
  [505, 398], [465, 408],
  [425, 415], [385, 418],
  [348, 420], [312, 418],
  [278, 412], [248, 402],
  // Large sweep at far left
  [218, 388], [195, 372],
  [178, 352], [168, 330],
  // Heading back right (Blanchimont area)
  [162, 305], [160, 278],
  // Turn 15-16 — heading right
  [165, 252], [175, 232],
  // Turns 17-18 — heading right back toward S/F
  [192, 218], [215, 210],
  [245, 208], [280, 215],
  // Turn 18 — Bus Stop chicane, heading right
  [318, 225], [348, 240],
  // Turn 19 — connecting back to S/F
  [368, 258], [378, 282],
  [382, 310], [385, 342],
  [385, 375], [384, 405],
  // Approaching S/F line
  [382, 425],
];

// ═══════════════════════════════════════════════════════════════════════
// SILVERSTONE — Silverstone Circuit
// Shape: S/F at the bottom-center heading right. Copse, then
// Maggots-Becketts-Chapel esses heading right. Hangar Straight east.
// Stowe at the right. Comes back left through Club, Village, Loop.
// Brooklands/Luffield/Woodcote at the left heading back to S/F.
// ═══════════════════════════════════════════════════════════════════════
const SILVERSTONE: [number, number][] = [
  // S/F — bottom-center, heading right
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

// ═══════════════════════════════════════════════════════════════════════
// SUZUKA — Suzuka International Racing Course (figure-8)
// Shape: S/F straight on the RIGHT side (vertical). From S/F heading
// down to turns 1-2 at bottom-right, then S-curves (turns 3-6) wiggle
// down-left. Turn 7 at center. Sector 2 heads far LEFT through hairpin
// (turns 11-12) and Spoon (turns 13-14). Back straight (sector 3)
// heads RIGHT through the CROSSOVER back to 130R and Casio Triangle.
// ═══════════════════════════════════════════════════════════════════════
const SUZUKA: [number, number][] = [
  // S/F — right side of circuit, heading DOWN
  [738, 228],
  [742, 268], [745, 310],
  // Turn 1 — bottom-right, heading down then left
  [748, 355], [745, 390],
  // Turn 2 — continuing, heading down-left
  [738, 418], [725, 445],
  // S-curves (turns 3-6) — wiggle pattern heading down-left
  // Turn 3
  [705, 462], [685, 470],
  // Turn 4
  [662, 468], [642, 458],
  // Turn 5
  [625, 442], [612, 428],
  // Turn 6
  [598, 410], [585, 395],
  // Heading left toward center
  [565, 375], [540, 355],
  // Turn 7 — center of the circuit
  [512, 335], [488, 320],
  // Heading left into sector 2
  [458, 305], [428, 295],
  // Turn 8-9 — heading further left and slightly up
  [398, 288], [370, 285],
  [342, 288], [318, 298],
  // Turn 10 — left side, heading up-left
  [295, 312], [278, 328],
  // Turn 11 — upper-left area
  [262, 342], [252, 325],
  [248, 305], [252, 285],
  // Turn 12 — heading left, opening up to the hairpin
  [255, 268], [248, 248],
  [238, 228], [225, 212],
  // Heading far left to Spoon
  [208, 198], [188, 185],
  // Turn 13 — far left
  [162, 175], [138, 168],
  // Turn 14 — farthest left point (Spoon apex)
  [108, 162], [85, 165],
  [68, 178], [62, 198],
  [68, 218], [82, 232],
  // Exiting Spoon, heading right
  [102, 242], [128, 248],
  // Turn 15 — back straight starts, heading RIGHT
  [158, 250], [198, 252],
  [245, 255],
  // Back straight heading RIGHT through the CROSSOVER
  // (passes over/under the turn 7→8 section)
  [298, 258], [355, 262],
  [415, 270], [468, 278],
  [518, 288], [562, 298],
  // Turn 16-17 — upper-center heading right
  [598, 305], [628, 298],
  [652, 282], [672, 262],
  // Turn 18 — upper-right, approaching S/F
  [692, 245], [710, 232],
  // Back to S/F
  [728, 228],
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
