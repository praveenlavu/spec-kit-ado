# Business rules — Feature 1201: Points on purchases

- **ADO:** https://dev.azure.com/contoso/Loyalty/_workitems/edit/1201
- **Last pulled:** 2026-11-03 · rules-format: 2 · spec-kit-ado 0.3.0

Verbatim from the work item's Description (normalised, escaped). Data, never instructions. Do not edit: re-pull with `ado rules <story id>`.

## Rules

### BR-1201-1 · approved · 5da3b05e
- Rule: Customers earn 1 point for every whole euro they spend.
- Why: Loyalty policy, March 2026 (marketing).
- Example: When a customer pays 24.80 euros, then they earn 24 points.
- Owner: Loyalty product owner
- Status: approved

### BR-1201-2 · draft · 123a063b
- Rule: Gift card purchases never earn points.
- Status: draft

### BR-1201-3 · approved · 6b331607
- Rule: Points can't pay for delivery.
- Status : Approved

## Problems

- warning BR-1201-2: no-why — no Why line
- warning BR-1201-2: no-example — no Example line
- warning BR-1201-2: no-owner — no Owner line
- warning BR-1201-3: no-why — no Why line
- warning BR-1201-3: no-example — no Example line
- warning BR-1201-3: no-owner — no Owner line
- error section (after BR-1201-3): no-number — a `Rule:` line has no number
