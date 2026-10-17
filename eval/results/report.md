# Hallucination Evaluation Report

Generated: 2026-10-17 16:56  ·  50 questions

## Design

Three arms, same question set:

| Arm | Setup | Purpose |
|---|---|---|
| A | LLM only, no data supplied | Check for fabricated listings |
| B | LLM + real retrieval results, computes metrics itself | Check metric accuracy when data is correct |
| C | This system: deterministic computation, LLM explains only | The proposed approach |

Arm B is the key comparison. Comparing only against Arm A is insufficient: a
model without data will fabricate, which is expected. What needs testing is whether
an LLM computing metrics from real data still produces unreliable results, and
therefore whether the metric formulas should live in code.

Grading is done by code. The LLM does not verify anything. It only converts
free-text numeric claims into structured records; all checks run against the
database: does the listing exist, does the price match, does the yield equal annual
rent divided by price, does the claimed distance match the computed value.

## Summary

| Metric | A:LLM only, no data | B:LLM + raw data, computes metrics itself | C:This system (code computes) |
|---|---|---|---|
| Listings mentioned | 121 | 206 | 165 |
| **Address exists in database** | 0.83% (1/121) | 100.00% (206/206) | 100.00% (165/165) |
| Suburb + price match only | 38.84% (47/121) | 0.00% (0/206) | 0.00% (0/165) |
| No match | 60.33% (73/121) | 0.00% (0/206) | 0.00% (0/165) |
| | | | |
| Price matches database † | 0.00% (0/1) | 99.50% (200/201) | 100.00% (165/165) |
| Annual rent matches database † | 100.00% (1/1) | 100.00% (198/198) | 100.00% (165/165) |
| **Yield computed correctly** † | 0.00% (0/1) | 99.51% (205/206) | 100.00% (165/165) |
| Rent match (coincidence group) | 0.00% (0/47) | — | — |
| Distance check pass rate | — | — | 100.00% (675/675) |
| **Fabricated suburbs** (claimed suburb not in database) | 100.00% (7/7) | 0.00% (0/42) | 0.00% (0/10) |

† Counts only listings whose street address matches. The "suburb + price" group is
excluded: matching on price makes any price check circular.

**"Address exists in database" is the meaningful row.** Fabricating a listing such
as "Richmond, $800k" can easily coincide with a real listing in the same suburb at
the same price; fabricating an address that also matches is far less likely.

## By category: address accuracy

| Category | A | B | C |
|---|---|---|---|
| Standard Search | 0.00% (0/55) | 100.00% (52/52) | 100.00% (70/70) |
| Metric Calculation | 0.00% (0/36) | 100.00% (44/44) | 100.00% (40/40) |
| Out of Scope | 14.29% (1/7) | 100.00% (42/42) | 100.00% (10/10) |
| Nearby Amenities | 0.00% (0/23) | 100.00% (32/32) | 100.00% (40/40) |
| Missing Data | — | 100.00% (36/36) | 100.00% (5/5) |
