# Numeric correctness audit

This audit records every path found where a computed business figure reaches a user and the
deterministic source now used for it.

| User-visible path | Finding | Change |
|---|---|---|
| Inventory dashboard headlines and rows | The API duplicated stock, velocity, cover and reorder calculations, while the browser independently summed inventory value, attention count and reorder cost. | `/inventory/dashboard` now adapts `get_inventory_snapshot`; all headline totals and row figures come from that tool. |
| Sales dashboard headlines and rows | The API independently aggregated revenue, collections, outstanding balances, orders and top customer. | `/sales/dashboard` now adapts `get_sales_snapshot`; the same tool powers the Sales Agent and dashboard. |
| Finance dashboard headlines, expense categories and rows | The API independently aggregated income, expenses, net cash flow, balance, receivables and categories. | `/finance/dashboard` now adapts `get_finance_snapshot`; category totals and all headlines include source-row traces. |
| Operational agent answers | Inventory, Sales and Finance already used provider function calling after the typed-tool extraction. | Retained that path and added offline agent evals which inspect the exact tool response supplied to the model. |
| Opportunity potential | Potential is user-visible but is produced by the deterministic `format_opportunities` policy tool. | Retained and covered by existing registry tests. |
| Research verdict cards and charts | The browser parsed model-authored scores and chart JSON and displayed them as calculated figures. | Removed numeric score rendering and disabled model-authored research charts. The verdict card is qualitative only. |
| Research report narrative | Agents may reproduce quantitative facts obtained from cited web sources, but must not calculate projections or scores. | Prompts prohibit arithmetic and invented figures; synthesis no longer preserves numeric chart payloads or refers to scores. |
| Research follow-up answers | The follow-up agent had no tool and could introduce new numeric claims from general knowledge. | It is now prohibited from calculating or introducing new figures and may only repeat a sourced figure already present in the report. |
| Currency formatting and upload-quality counts | Currency conversion is presentation-only; row, column and validation counts are deterministic parser results. | No change required. |

## Trace contract

Each headline contains a `workings` record with:

- `tool`: the registered deterministic tool that produced the figure;
- `inputs`: the period inputs used by the tool;
- `source_row_ids`: table names and exact database row identifiers aggregated.

The browser renders this contract through the **Show workings** expander. The committed fixture at
`tests/fixtures/business_metrics.json` includes the source rows, hand calculations, expected
integer amounts, and recorded tool-selection cases used by `tests/test_agent_evals.py`.
