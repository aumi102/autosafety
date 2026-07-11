# 00 — Project Brief

## Name

**AutoSafety GraphSQL Copilot**

## Core idea

An AI analyst copilot for public vehicle-safety data. Users ask natural-language questions about vehicle complaints, recalls, investigations, manufacturer communications, and vehicle metadata. The system decides whether to use SQL analytics, graph retrieval, semantic text retrieval, or a hybrid workflow.

## Product positioning

Use these phrases:

- Vehicle safety research assistant
- Public data investigation copilot
- Recall and complaint analytics tool
- Evidence-grounded automotive safety explorer

Avoid these phrases:

- “This car is unsafe.”
- “This complaint proves a defect.”
- “This recall caused these complaints.”
- “Official NHTSA conclusion” unless the exact source record says so.

## Target users

### 1. Data analyst

Wants counts, trends, rankings, component distributions, and repeatable SQL-backed summaries.

### 2. Automotive safety researcher

Wants to explore relationships between complaints, recalls, investigations, and manufacturer communications.

### 3. Consumer information user

Wants a readable explanation of public records for a specific make/model/year. The system must frame output as informational, not as legal, purchasing, or safety certification advice.

### 4. Recruiter / portfolio evaluator

Wants to see production-grade capability across GraphRAG, Text-to-SQL, backend architecture, evaluation, observability, and UX.

## Supported question types

### SQL-only

Use when the user asks for counts, rankings, filters, comparisons, or trends.

Examples:

```text
Top 10 components with most complaints for Ford F-150 2022.
How many injury-related complaints were reported for Toyota Camry 2020?
Compare brake complaints across Honda Accord model years 2020–2024.
Which makes had the most recalls in 2025?
```

### GraphRAG-only

Use when the user asks for text evidence, consequence, remedy, narrative, investigation summary, or manufacturer communication summary.

Examples:

```text
What remedy did NHTSA describe for campaign 22V176000?
What consequence is described in this recall?
Summarize the investigation narrative for this defect.
```

### Hybrid SQL + GraphRAG

Use when the user asks to first find a statistical pattern and then verify or explain it using evidence records.

Examples:

```text
Find vehicle models with rising steering complaints from 2020 to 2026, then check whether they have related recalls or investigations.
Which component had the most complaints for Tesla Model 3 2022, and is there a recall related to that component?
Show the complaint trend for brake issues in Honda Accord 2021 and summarize official recall or investigation evidence.
```

## MVP scope

### Years

2020–2026.

### Makes

Start with 10 makes:

```text
Toyota, Honda, Ford, Tesla, Chevrolet, Hyundai, Kia, Nissan, BMW, Mercedes-Benz
```

### Data groups

```text
complaints
recalls
investigations
manufacturer_communications
vehicle metadata
```

### MVP success criterion

A browser user can ask one hybrid question and receive:

1. a SQL-backed result table,
2. a cited evidence summary,
3. graph paths connecting vehicle/component/evidence records,
4. a caveat separating public-record association from official causality,
5. a persisted agent run with tool-call logs.
