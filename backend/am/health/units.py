"""Conversion of entered units to each metric's canonical unit.

Only exact or physically defined conversions belong here, and every constant states where
it comes from. Conversion runs BEFORE validation, so sanity bounds, baselines and anomaly
rules only ever see canonical values. The value and unit as entered are kept on the
measurement (`original_value` / `original_unit`), so every stored number can be traced back
to what a person typed.
"""

from __future__ import annotations

from dataclasses import dataclass

from am.health.metrics import MetricSpec, normalize_unit

# Fahrenheit to Celsius: t/°C = (t/°F - 32) / 1.8. Exact by the definition of the two
# scales (NIST Special Publication 811, 2008 edition, Appendix B.8).
FAHRENHEIT_ZERO_OFFSET = -32.0
FAHRENHEIT_TO_CELSIUS = 5.0 / 9.0

# Pound (avoirdupois) to kilogram: exactly 0.45359237 kg, fixed by the 1959 international
# yard and pound agreement (NIST Special Publication 811, 2008 edition, Appendix B.8).
POUND_TO_KILOGRAM = 0.45359237

# Glucose (C6H12O6) molar mass 180.16 g/mol, from IUPAC standard atomic weights
# (C 12.011, H 1.008, O 15.999). 1 mmol/L = 180.16 mg/L = 18.016 mg/dL (1 dL = 0.1 L).
GLUCOSE_MOLAR_MASS_G_PER_MOL = 180.16
MMOL_PER_L_TO_MG_PER_DL = GLUCOSE_MOLAR_MASS_G_PER_MOL / 10.0

# Microsievert to millisievert: SI prefixes micro (1e-6) and milli (1e-3), exact
# (BIPM, The International System of Units, 9th edition, 2019, Table 7).
MICROSIEVERT_TO_MILLISIEVERT = 1e-3

# Converted values keep 12 significant digits. That strips binary floating-point noise
# (98.6 °F gives 37 °C, not 36.99999999999999) and stays far finer than any instrument.
SIGNIFICANT_DIGITS = 12

# How the UI shows canonical unit codes that are not plain symbols.
CANONICAL_LABELS = {"degC": "°C", "count": "steps"}


def _round_significant(x: float) -> float:
    return float(f"{x:.{SIGNIFICANT_DIGITS}g}")


def _same_unit(a: str, b: str) -> bool:
    # casefold() also maps the micro sign (U+00B5) to the Greek mu (U+03BC): both spellings
    # of µSv are in use and must mean the same unit.
    return a.strip().casefold() == b.strip().casefold()


@dataclass(frozen=True)
class UnitConversion:
    """Linear conversion into a metric's canonical unit: canonical = (value + offset) * factor."""

    unit: str  # input unit code, stored as `original_unit`
    label: str  # how the unit is shown to people
    factor: float
    offset: float = 0.0
    aliases: tuple[str, ...] = ()
    note: str = ""  # the formula in words, shown next to the result
    source: str = ""  # where the constant comes from

    def matches(self, unit: str) -> bool:
        return any(_same_unit(unit, u) for u in (self.unit, *self.aliases))

    def to_canonical(self, value: float) -> float:
        return _round_significant((value + self.offset) * self.factor)


CONVERSIONS: dict[str, tuple[UnitConversion, ...]] = {
    "body_temperature": (
        UnitConversion(
            "degF", "°F", FAHRENHEIT_TO_CELSIUS, FAHRENHEIT_ZERO_OFFSET,
            aliases=("°F", "F", "deg F", "fahrenheit"),
            note="°C = (°F − 32) × 5/9",
            source="Exact, by definition of the Celsius and Fahrenheit scales (NIST SP 811, appendix B.8).",
        ),
    ),
    "body_mass": (
        UnitConversion(
            "lb", "lb", POUND_TO_KILOGRAM,
            aliases=("lbs", "pound", "pounds"),
            note="kg = lb × 0.45359237",
            source="Exact, international avoirdupois pound of 1959 (NIST SP 811, appendix B.8).",
        ),
    ),
    "blood_glucose": (
        UnitConversion(
            "mmol/L", "mmol/L", MMOL_PER_L_TO_MG_PER_DL,
            aliases=("mmol/l", "mmol per L"),
            note="mg/dL = mmol/L × 18.016",
            source="Glucose molar mass 180.16 g/mol (IUPAC standard atomic weights).",
        ),
    ),
    "radiation_dose": (
        UnitConversion(
            "uSv", "µSv", MICROSIEVERT_TO_MILLISIEVERT,
            aliases=("µSv", "μSv", "microsievert", "microsieverts"),
            note="mSv = µSv ÷ 1000",
            source="Exact, SI prefixes micro and milli (BIPM SI Brochure, 9th edition).",
        ),
    ),
}


@dataclass(frozen=True)
class Canonical:
    """A value expressed in the metric's canonical unit, with the conversion that produced it."""

    value: float
    unit: str
    conversion: UnitConversion | None = None
    original_value: float | None = None

    @property
    def original_unit(self) -> str | None:
        return self.conversion.unit if self.conversion else None


def conversions_for(metric_code: str) -> tuple[UnitConversion, ...]:
    return CONVERSIONS.get(metric_code, ())


def find_conversion(metric_code: str, unit: str) -> UnitConversion | None:
    return next((c for c in conversions_for(metric_code) if c.matches(unit)), None)


def canonical_label(unit: str) -> str:
    return CANONICAL_LABELS.get(unit, unit)


def to_canonical(spec: MetricSpec, value: float, unit: str) -> Canonical | None:
    """Express `value` in the canonical unit of `spec`; None when the unit is not accepted.

    The canonical unit or one of its aliases leaves the value untouched (no conversion, so
    no original value is recorded). A convertible unit is converted. Anything else is
    unknown and must be rejected by validation rather than guessed.
    """
    canonical = normalize_unit(spec, unit)
    if canonical is not None:
        return Canonical(value, canonical)
    conv = find_conversion(spec.code, unit)
    if conv is None:
        return None
    return Canonical(conv.to_canonical(value), spec.unit, conversion=conv, original_value=value)


def accepted_unit_codes(spec: MetricSpec) -> list[str]:
    return [spec.unit, *(c.unit for c in conversions_for(spec.code))]
