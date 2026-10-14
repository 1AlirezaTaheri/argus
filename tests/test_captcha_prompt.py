#!/usr/bin/env python3
"""The CAPTCHA question must never wrap, and must never be squeezed.

## Why this is a test and not a comment

The defect was found on a screenshot, not in review, and every rule involved was
correct in isolation:

    <div className="flex items-stretch gap-2">
      <span className="inset-well flex select-none items-center ... px-3 font-mono text-sm text-fg">
        {challenge.question}
      </span>
      <Input ... />          {/* .input sets width: 100% */}
    </div>

A flex item's default `flex-shrink` is 1. The input's `width: 100%` is the
largest size either half could want, so the input takes the space and the prompt
span is squeezed to a sliver -- "10 - 9 = ?" became "10 - 9" / "= ?". Nothing
about any single class was wrong; only how they negotiated.

A comment saying "don't wrap this" would not have survived the next refactor
that touched the row. The classes are asserted instead.

## The second half: nowrap must stay safe

`whitespace-nowrap` on an unbounded string trades one bug for a worse one --
overflow. So the test also reads the generator and reconstructs the longest
question from the same format string the service uses, rather than trusting a
hardcoded `"19 - 19 = ?"`. If the operands are ever widened, this fails and
says why, instead of letting the prompt crowd the answer field on a phone.

The width arithmetic is an estimate (font-mono text-sm, `px-3`, one border) and
is labelled as such. It is not a substitute for looking at the rendered page --
`tools/verify-render.sh` and the screenshots are what actually prove it -- but
it catches the operand change that would break it.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
LOGIN = REPO / "admin" / "frontend" / "src" / "pages" / "login.tsx"
CAPTCHA = REPO / "admin" / "services" / "captcha.py"


def _prompt_span() -> str:
    """The element that renders `challenge.question`."""
    body = LOGIN.read_text(encoding="utf-8")
    m = re.search(r'<span[^>]*>\s*\{challenge\.question\}', body)
    assert m, (
        "the CAPTCHA prompt span is no longer where it was expected -- "
        "find the element rendering challenge.question and update this test"
    )
    return m.group(0)


def _answer_input() -> str:
    body = LOGIN.read_text(encoding="utf-8")
    m = re.search(
        r'<Input[^>]*id="captcha-answer"[^>]*/>', body, re.DOTALL
    )
    assert m, "the CAPTCHA answer input is no longer where it was expected"
    return m.group(0)


def test_the_prompt_cannot_be_squeezed_by_the_input() -> None:
    """`shrink-0` is what stops the w-full input taking the row's width.

    Without it the span is a flex item with the default `flex-shrink: 1`, and
    the input's `width: 100%` outbids it. The span then collapses to whatever is
    left -- about 70px -- and the equation wraps.
    """
    span = _prompt_span()
    assert "shrink-0" in span, (
        "the CAPTCHA prompt must not shrink; the answer input is w-full and "
        "would otherwise squeeze the question into two lines"
    )


def test_the_equation_never_breaks() -> None:
    """`whitespace-nowrap` is the guarantee, not a hope.

    `shrink-0` alone would still allow a break if the span were ever narrower
    than its content for another reason -- a narrower card, a longer operand
    pair, a different font. The class makes the break impossible.
    """
    span = _prompt_span()
    assert "whitespace-nowrap" in span, (
        "the CAPTCHA question must never wrap; it is an equation and a break "
        "mid-equation reads as a rendering fault"
    )


def test_the_answer_input_can_take_the_remaining_width() -> None:
    """`min-w-0` lets the input shrink below its intrinsic width.

    An input refuses to go narrower than its default `size` attribute. Without
    `min-w-0` the row would overflow the card on a narrow screen rather than
    giving the space back to the field that needs it.
    """
    assert "min-w-0" in _answer_input(), (
        "the answer input must be allowed to shrink, or the row overflows "
        "instead of the prompt keeping its width"
    )


def test_the_prompt_is_a_well_matching_the_input_beside_it() -> None:
    """The two halves of one control must read as one control.

    This is the reasoning recorded at the call site: the prompt was once
    `bg-muted/40`, which on the dark theme renders *lighter* than the card, so
    the question looked like a card sitting on the card and the layering read
    backwards. It is asserted because the visual is the whole point and a
    background swap is a one-token change nobody would think to check.
    """
    span = _prompt_span()
    assert "inset-well" in span, "the prompt must be a well, not a panel"
    assert "bg-muted" not in span, (
        "bg-muted/40 is lighter than the card on the dark theme, which made the "
        "question read as a card sitting on the card"
    )


def test_nowrap_cannot_overflow_at_any_operand_pair() -> None:
    """The longest question must still leave room for the answer field.

    `whitespace-nowrap` is only correct while the question is short enough to
    fit. The bounds are read from the generator and the longest question is
    reconstructed from its format string, so widening the operands fails here
    rather than overflowing on a phone at some later date.

    The width is an estimate: font-mono text-sm is roughly 8px per character,
    plus `px-3` (24px) and a 1px border on each side. It is deliberately
    conservative -- it is a tripwire, not a measurement.
    """
    src = CAPTCHA.read_text(encoding="utf-8")

    lo = re.search(r"^_MIN_OPERAND\s*=\s*(\d+)$", src, re.MULTILINE)
    hi = re.search(r"^_MAX_OPERAND\s*=\s*(\d+)$", src, re.MULTILINE)
    assert lo and hi, "could not find the operand bounds in captcha.py"

    fmt = re.search(r'question = f"\{a\} ([+-]) \{b\} = \?"', src)
    assert fmt, (
        "the question format string no longer matches "
        '`f"{a} {op} {b} = ?"` -- update this test to the new format'
    )

    longest = f"{hi.group(1)} {fmt.group(1)} {hi.group(1)} = ?"
    estimate = len(longest) * 8.0 + 32.0

    # The narrowest card content area this can appear in: a 390px phone viewport
    # with the card's padding on both sides.
    narrowest_card = 390 - 2 * 32

    assert estimate < narrowest_card * 0.6, (
        f"the longest question is {longest!r}, about {estimate:.0f}px, which "
        f"would crowd the answer field on a {narrowest_card}px card. "
        "whitespace-nowrap is no longer safe -- either narrow the operands or "
        "let the prompt wrap deliberately"
    )


def test_the_generator_is_the_one_this_test_describes() -> None:
    """Guard against the test drifting from the code it describes.

    Every assertion above is about a specific format string and two constants.
    If the generator is rewritten, the test should fail and be updated, not
    quietly pass against code it no longer understands.
    """
    src = CAPTCHA.read_text(encoding="utf-8")
    assert "random.randint(_MIN_OPERAND, _MAX_OPERAND)" in src, (
        "the operands are no longer drawn from _MIN_OPERAND.._MAX_OPERAND"
    )
    assert src.count('question = f"{a}') == 2, (
        "expected exactly two question formats (addition and subtraction)"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
