"""Render approved amount semantics on the user's unchanged latest HTML source."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "frontend-e2e/fixtures/latest-html-template.html"
TEMPLATE_SHA = "fdaee02db163a3f15285842e66a1047ae3fd8d62e4c110a28fa562f51b878501"
SCRIPT_SHA = "099c830d99badd0b6f7d3efc76a6c7a79f15534d87926d29f64e6d5a3b1b94ae"
BASE = ROOT / "review-input/us-politician-trades-watch/scripts/render_dashboard.py"
if hashlib.sha256(BASE.read_bytes()).hexdigest() != "ae3fc8a38cdc1df7c7ddff3b9f3f834401763d09d5b9891e56022b786f3f6c0c":
    raise ValueError("Frozen renderer helpers changed")
spec = importlib.util.spec_from_file_location("_private_original_portrait_helpers", BASE)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
load_dashboard_data = helpers.load_dashboard_data


def approved_template() -> str:
    raw = TEMPLATE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TEMPLATE_SHA:
        raise ValueError("Highest HTML template changed; review approved differences")
    text = raw.decode("utf-8")
    changes = {
        "const range = t => `${money(t.amount_low)}–${money(t.amount_high)}`;": "const range = t => approvedAmountRange(t);",
        "function render() {": "function renderApprovedBase() {",
        "function renderPersonPage() {": "function renderApprovedPersonBase() {",
        "function renderStockPage() {": "function renderApprovedStockBase() {",
    }
    for old, new in changes.items():
        if text.count(old) != 1:
            raise ValueError("Highest HTML approval patch boundary changed")
        text = text.replace(old, new)
    for value in ("agg", "item", "buyer", "row.agg"):
        text = text.replace("${money(" + value + ".low)}–${money(" + value + ".high)}",
                            "${approvedAggregateRange(" + value + ")}")
    text = text.replace("${compactMoney(s.low)}–${compactMoney(s.high)}", "${approvedAggregateRange(s, true)}")
    text = text.replace("${money(low)}–${money(high)}", "${approvedAggregateRange({...approvedTotals(related),low,high})}")
    text = text.replace("Number(t.amount_low || 0)", "approvedAmountValue(t, 'low')")
    text = text.replace("Number(t.amount_high || 0)", "approvedAmountValue(t, 'high')")
    # An amount-only sort excludes groups whose disclosures all lack an upper bound.
    text = text.replace("sortHotRows(rows, hotSort[days].key, hotSort[days].direction)",
                        "sortHotRows(hotSort[days].key === 'amount' ? rows.filter(row => row.related.some(t => !approvedIsOpen(t))) : rows, hotSort[days].key, hotSort[days].direction)")
    script_bytes = Path(__file__).with_name("open_amounts.js").read_bytes()
    if hashlib.sha256(script_bytes).hexdigest() != SCRIPT_SHA:
        raise ValueError("Approved amount display script changed")
    script = script_bytes.decode("utf-8")
    if text.count("</script>") != 1:
        raise ValueError("Highest HTML script boundary changed")
    return text.replace("</script>", script + "\n</script>")


def render_html(data: dict) -> str:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c")
    text = approved_template().replace("__LATEST_HTML_DATA_JSON__", encoded)
    return text.replace("const PEOPLE = ", "const IS_DEMO = " + ("true" if data["meta"].get("is_demo") else "false") + ";\nconst PEOPLE = ", 1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(load_dashboard_data(args.input)), encoding="utf-8")


if __name__ == "__main__":
    main()
