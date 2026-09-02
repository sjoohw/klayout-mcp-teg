"""One deterministic micron-to-DBU conversion policy for public geometry inputs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN


DEFAULT_GRID_TOLERANCE_DBU = Decimal("1e-9")


class DbuGridError(ValueError):
    """Raised when a value is invalid or materially off the requested DBU grid."""


def micron_to_dbu(
    value_um: object,
    dbu_um: object,
    *,
    tolerance_dbu: object = DEFAULT_GRID_TOLERANCE_DBU,
) -> int:
    """Return the nearest DBU for only sub-nanodbu numeric representation drift.

    The tolerance is expressed in DBU units, not microns.  It accepts artifacts such
    as ``0.1 + 0.2`` while still rejecting a physically different off-grid coordinate.
    """

    if isinstance(value_um, bool) or isinstance(dbu_um, bool) or isinstance(tolerance_dbu, bool):
        raise DbuGridError("boolean values are not valid dimensions")
    try:
        value = Decimal(str(value_um))
        dbu = Decimal(str(dbu_um))
        tolerance = Decimal(str(tolerance_dbu))
    except (InvalidOperation, ValueError) as exc:
        raise DbuGridError("value, DBU, and tolerance must be decimal numbers") from exc
    if not value.is_finite() or not dbu.is_finite() or dbu <= 0:
        raise DbuGridError("value must be finite and DBU must be finite and positive")
    if not tolerance.is_finite() or tolerance < 0:
        raise DbuGridError("DBU tolerance must be finite and non-negative")
    units = value / dbu
    nearest = units.to_integral_value(rounding=ROUND_HALF_EVEN)
    if abs(units - nearest) > tolerance:
        raise DbuGridError("value is off the requested DBU grid")
    return int(nearest)


def is_on_dbu_grid(
    value_um: object,
    dbu_um: object,
    *,
    tolerance_dbu: object = DEFAULT_GRID_TOLERANCE_DBU,
) -> bool:
    try:
        micron_to_dbu(value_um, dbu_um, tolerance_dbu=tolerance_dbu)
    except DbuGridError:
        return False
    return True
