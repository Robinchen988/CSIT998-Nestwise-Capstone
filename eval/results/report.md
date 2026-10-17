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

## Findings

### 1. Evaluation of Listing Accuracy Without External Data

Arm A mentioned 121 listings. Only 1 (1%) had an address that exists in the database, indicating that the model systematically fabricated almost all listings when no external data was provided. Furthermore, when asked about Sydney or Brisbane, 100% of the listings it produced claimed suburbs that do not exist in the database (such as Liverpool or Parramatta), with prices and yields specified to the dollar.

### 2. Evaluation of LLM Arithmetic Accuracy

The evaluation was designed expecting Arm B to make calculation errors, which would support the claim that formulas must live in code. The data does not support that expectation. Arm B mentioned 206 listings, of which 100% (206/206) had valid addresses. Among the claims eligible for verification, price accuracy was 99.50% (200/201), and rental accuracy was 100% (198/198). The yield was computed correctly in 99.51% (205/206).

These findings indicate that the LLM performs reliably on basic arithmetic tasks when supplied with verified property data. Therefore, the main architectural advantage should not be attributed solely to arithmetic accuracy.

### 3. Key Differences in Retrieval and Ranking Performance

(a) Questions of the form "highest yield under $X": Arm B is systematically wrong, even though every arithmetic step is correct.

For queries requiring global ranking (e.g., the highest-yield property under a certain price limit), Arm B only sees the 10 semantic retrieval results placed in its prompt. Every number it calculates may be correct, but the answer is often wrong because the true highest-yield property is not within those 10 candidates. "Highest" requires comparison over the full set, which cannot be solved by placing a small batch of data in the context window. It is a retrieval-and-ranking architecture problem.

An earlier version of Arm C had the same issue. It ranked only within 120 semantic candidates. A serious case: for "three-bedroom house under $1M", 4,370 properties in the database match, and the "5 quietest" it returned ranked 85th, 89th, 103rd, 113th and 243rd in the full set, with zero overlap with ground truth.

The fix was to raise the candidate pool from 120 to 5,000, and to compute amenity and environmental evidence in batch (BallTree / STRtree), since a larger pool would otherwise add over ten seconds to each query. Arm C now reports the full-set ground truth for these questions.

The retrieval process is subject to a maximum candidate pool of 5,000 properties, rather than the complete dataset of 20,800 records. When more than 5,000 properties pass the hard filters, ranking still runs on a subset, and every output notes this. Guaranteeing full-set optimality at any scale would require search_properties() to sort by metric, which changes its signature. This modification would require coordinated changes to the retrieval interface and its dependent modules.

(b) When data is missing, Arm B has only two options: fabricate or refuse. Arm C has a third.

For questions requiring data not present in the dataset (e.g., NOI and cap rate), Arm B typically refuses to answer because the records do not provide the necessary operating expenses. Arm C, however, can compute these figures using a labelled assumption (e.g., a 28% operating expense rate) and explicitly states that the figures are based on an assumed rate, not actual transaction data.

The third option is an explicitly labelled assumption: a number is given, the basis is stated, and the user can change it (`:opex 30`). Using assumptions is not the problem; presenting them as data is.

### 4. A case Arm C did not catch

Question o5 asks for houses on "Zhongshan Road". The system returned 5 real listings (e.g., in Hillside) but did not explicitly state that Melbourne has no such street. 

The cause is that street name is not a filterable dimension. `search_properties()` supports exact filtering on suburb, so "Sydney" and "Brisbane" can be rejected by the database directly. It has no street parameter, so a street name only falls through to semantic search and degrades to fuzzy matching.

This is not fabrication (all 5 listings exist), but it does not answer the question directly. Fixing it requires adding a street dimension to the retrieval layer. This modification would also require coordinated changes to the retrieval interface and its dependent modules.

### 5. Reliability of the evaluation itself

- Grading is not done by an LLM. The LLM only converts free text into structured records; every check runs against the database in code and can be re-run and re-inspected.
- Verifier bugs count as hallucinations in the system under test, so the verifier has to be checked first. The first version required both suburb and address to match, which marked real Arm B listings as fabricated because Arm B listed them in a table with the suburb shown only once. The fix allows a unique street address to match on its own, and Arm B's address accuracy reached 100%.
- "Suburb + price" matches are excluded from the main metric. Fabricating "Richmond, $800k" can easily coincide with one of the 328 Richmond listings at that price, and the price check would then be circular. The main metric therefore counts only street address matches.
- The evaluation metric counts all listing claims mentioned in a response, not just those in a formal recommendation list. In cases where an arm explained missing data but provided a supplementary table, those listings were also verified.
- When a street address corresponds to multiple records in the database, the verifier prioritises matching by price. This means the 100% price accuracy should be interpreted as the accuracy within this specific matching method, rather than an independent verification across all possible conditions.
- Sample size is limited: 50 questions, 3 arms, 492 listing claims. Differences between categories (especially small sub-samples) are not statistically robust.
