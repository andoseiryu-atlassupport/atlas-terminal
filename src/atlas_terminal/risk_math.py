"""Small bounded Decimal linear algebra; no binary floating point or external engine."""

from decimal import Decimal

from atlas_terminal.errors import AtlasError

ZERO, ONE = Decimal(0), Decimal(1)


def text(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if value == 0 else result


def dot(left: list[Decimal], right: list[Decimal]) -> Decimal:
    return sum((a * b for a, b in zip(left, right, strict=True)), ZERO)


def matvec(matrix: list[list[Decimal]], vector: list[Decimal]) -> list[Decimal]:
    return [dot(row, vector) for row in matrix]


def solve(matrix: list[list[Decimal]], vector: list[Decimal]) -> tuple[list[Decimal], Decimal]:
    """Partial-pivot elimination; reject a deficient design rather than invent coefficients."""
    n = len(vector)
    work = [row[:] + [value] for row, value in zip(matrix, vector, strict=True)]
    scale = max(abs(value) for row in matrix for value in row)
    minimum = ONE
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(work[row][col]))
        ratio = abs(work[pivot][col]) / scale if scale else ZERO
        minimum = min(minimum, ratio)
        if ratio < Decimal("1e-10"):
            raise AtlasError(
                "RISK_RANK_DEFICIENT",
                "Risk exposures are collinear or ill-conditioned; revise styles or universe.",
            )
        work[col], work[pivot] = work[pivot], work[col]
        divisor = work[col][col]
        for j in range(col, n + 1):
            work[col][j] /= divisor
        for row in range(col + 1, n):
            multiplier = work[row][col]
            for j in range(col, n + 1):
                work[row][j] -= multiplier * work[col][j]
    result = [ZERO] * n
    for i in reversed(range(n)):
        result[i] = work[i][n] - dot(work[i][i + 1 : n], result[i + 1 :])
    return result, minimum


def wls(
    rows: list[list[Decimal]], returns: list[Decimal], weights: list[Decimal]
) -> tuple[list[Decimal], list[Decimal], Decimal]:
    width = len(rows[0])
    gram = [[ZERO] * width for _ in range(width)]
    rhs = [ZERO] * width
    for row, value, weight in zip(rows, returns, weights, strict=True):
        for i in range(width):
            rhs[i] += weight * row[i] * value
            for j in range(i, width):
                gram[i][j] += weight * row[i] * row[j]
    for i in range(width):
        for j in range(i):
            gram[i][j] = gram[j][i]
    factors, minimum = solve(gram, rhs)
    return factors, [r - dot(x, factors) for r, x in zip(returns, rows, strict=True)], minimum


def ewma_weights(length: int, decay: Decimal) -> list[Decimal]:
    values = [decay ** (length - 1 - i) for i in range(length)]
    total = sum(values, ZERO)
    return [w / total for w in values]


def covariance(
    rows: list[list[Decimal]], weights: list[Decimal], shrinkage: Decimal = ZERO
) -> list[list[Decimal]]:
    """Centered weighted sample covariance, shrunk toward its own diagonal (PSD)."""
    width = len(rows[0])
    means = [dot([row[i] for row in rows], weights) for i in range(width)]
    denominator = ONE - dot(weights, weights)
    result = [[ZERO] * width for _ in range(width)]
    for row, weight in zip(rows, weights, strict=True):
        centered = [value - mean for value, mean in zip(row, means, strict=True)]
        for i in range(width):
            for j in range(i, width):
                result[i][j] += weight * centered[i] * centered[j] / denominator
    for i in range(width):
        for j in range(i + 1, width):
            result[i][j] *= ONE - shrinkage
            result[j][i] = result[i][j]
    return result


def quantile(values: list[Decimal], fraction: Decimal) -> Decimal:
    ordered = sorted(values)
    point = fraction * (len(ordered) - 1)
    lower = int(point)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (point - lower) * (ordered[upper] - ordered[lower])


def standardize(
    values: list[Decimal], weights: list[Decimal]
) -> tuple[list[Decimal], dict[str, str]]:
    low, high = quantile(values, Decimal("0.05")), quantile(values, Decimal("0.95"))
    clipped = [min(high, max(low, value)) for value in values]
    center = dot(clipped, weights)
    variance = dot([(value - center) ** 2 for value in clipped], weights)
    if variance <= Decimal("1e-24"):
        raise AtlasError(
            "RISK_RANK_DEFICIENT", "A selected style has no usable cross-sectional spread."
        )
    scale = variance.sqrt()
    return [(value - center) / scale for value in clipped], {
        "clip_p05": text(low),
        "clip_p95": text(high),
        "weighted_center": text(center),
        "weighted_scale": text(scale),
    }
