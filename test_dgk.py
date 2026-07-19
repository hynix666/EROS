"""DGK kernel self-test — EROS v3.2 Phase 0 Day 6: "Kernel self-test 18/18".

Covers every adversarial variant class the Gold Set spec (§6.8.1) names:
scale_unified, percent_vs_decimal, iso_date, dmy_vs_mdy, fuzzy_entity /
ocr_noise, normalized_quote, paraphrase — plus the negative (must-prove-
UNGROUNDED) faces of each. Pure-Python; no database required.
"""
from __future__ import annotations

import pytest

from eros.dgk.kernel import (
    ENTITY_TOLERANCE,
    NUMBER_TOLERANCE,
    check_claim,
    extract_dates,
    extract_numbers,
)

pytestmark = pytest.mark.dgk


# ═════════════════════════════════════════════════════════════════════════
# Grounded faces — the kernel must NOT prove these ungrounded (INDETERMINATE)
# ═════════════════════════════════════════════════════════════════════════

def test_01_scale_unified_word_vs_digits():
    r = check_claim(
        "Acme Corp revenue was 1.5 billion dollars.",
        ["Acme Corp revenue was 1,500,000,000 dollars in the filing."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_02_scale_suffix_b_vs_word():
    r = check_claim(
        "Acme Corp posted revenue of $2.5B.",
        ["Acme Corp posted revenue of 2.5 billion dollars."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_03_percent_vs_decimal():
    r = check_claim(
        "Acme Corp margin was 10% for the year.",
        ["Acme Corp margin was 0.1 for the year."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_04_decimal_vs_percent():
    r = check_claim(
        "Acme Corp margin was 0.25 for the quarter.",
        ["Acme Corp margin was 25% for the quarter."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_05_iso_date_vs_mdy():
    r = check_claim(
        "The Acme Corp merger closed on 2024-06-05.",
        ["The Acme Corp merger closed on June 5, 2024."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_06_dmy_vs_mdy():
    r = check_claim(
        "The Acme Corp merger closed on 5 June 2024.",
        ["The Acme Corp merger closed on June 5, 2024."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_07_partial_date_first_of_month():
    r = check_claim(
        "Acme Corp announced the deal in June 2024.",
        ["Acme Corp announced the deal on 2024-06-01."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_08_entity_ocr_noise_within_tolerance():
    r = check_claim(
        "Microsoft reported strong quarterly results.",
        ["Micros0ft reported strong quarterly results."],  # OCR zero
    )
    assert r.verdict == "INDETERMINATE", r


def test_09_normalized_quote_curly_vs_straight():
    r = check_claim(
        "Alice said \u201ccuriouser  and curiouser\u201d about the finding.",
        ['In the report Alice said "curiouser and curiouser!" about the finding.'],
    )
    assert r.verdict == "INDETERMINATE", r


def test_10_number_within_half_percent_tolerance():
    r = check_claim(
        "Acme Corp revenue was 1.503 billion dollars.",
        ["Acme Corp revenue was 1.5 billion dollars."],  # 0.2% delta
    )
    assert r.verdict == "INDETERMINATE", r


def test_11_faithful_paraphrase_no_anchors_missing():
    r = check_claim(
        "Acme Corp saw substantial revenue growth according to the filing.",
        ["Acme Corp reported revenue of $1.5 billion, up sharply, in its filing."],
    )
    assert r.verdict == "INDETERMINATE", r


def test_12_entity_possessive_lemmatised():
    r = check_claim(
        "Tesla's deliveries rose in the quarter.",
        ["Tesla announced that deliveries rose in the quarter."],
    )
    assert r.verdict == "INDETERMINATE", r


# ═════════════════════════════════════════════════════════════════════════
# Ungrounded faces — the kernel must PROVE these ungrounded
# ═════════════════════════════════════════════════════════════════════════

def test_13_wrong_number_scale_unified():
    r = check_claim(
        "Acme Corp revenue was 2.5 billion dollars.",
        ["Acme Corp revenue was 1,500,000,000 dollars."],
    )
    assert r.verdict == "UNGROUNDED"
    assert r.missing_numbers, r


def test_14_wrong_date():
    r = check_claim(
        "The merger closed on July 4, 2023.",
        ["The merger closed on June 5, 2024."],
    )
    assert r.verdict == "UNGROUNDED"
    assert "2023-07-04" in r.missing_dates, r


def test_15_fabricated_entity():
    r = check_claim(
        "Globex Corporation acquired the startup.",
        ["Acme Corp acquired the startup last year."],
    )
    assert r.verdict == "UNGROUNDED"
    assert any("Globex" in e for e in r.missing_entities), r


def test_16_fabricated_quotation():
    r = check_claim(
        'The CEO said "the moon is made of cheese" at the meeting.',
        ["The CEO discussed quarterly targets at the meeting."],
    )
    assert r.verdict == "UNGROUNDED"
    assert r.missing_quotations, r


def test_17_number_beyond_tolerance():
    r = check_claim(
        "Acme Corp revenue was 1.6 billion dollars.",
        ["Acme Corp revenue was 1.5 billion dollars."],  # 6.7% > 0.5%
    )
    assert r.verdict == "UNGROUNDED"
    assert r.missing_numbers, r


def test_18_entity_beyond_fuzzy_tolerance():
    r = check_claim(
        "Macrohard reported strong results.",
        ["Microsoft reported strong results."],  # distance ≈ 0.56 > 0.25
    )
    assert r.verdict == "UNGROUNDED"
    assert any("Macrohard" in e for e in r.missing_entities), r


# ═════════════════════════════════════════════════════════════════════════
# Structural invariants (not counted in the 18 — cheap sanity)
# ═════════════════════════════════════════════════════════════════════════

def test_tolerances_recorded_in_result():
    r = check_claim("x", ["x"])
    assert r.number_tolerance == NUMBER_TOLERANCE == 0.005
    assert r.entity_tolerance == ENTITY_TOLERANCE == 0.25


def test_date_masking_prevents_number_double_extraction():
    dates, masked = extract_dates("It happened in June 2024 exactly.")
    assert dates == {"2024-06-01"}
    assert not extract_numbers(masked), "date components must not re-extract as numbers"


def test_scale_suffix_word_boundary():
    # "5 kg" must NOT parse as 5 thousand
    nums = extract_numbers("the sample weighed 5 kg")
    assert nums and nums[0][0] == 5.0
