# 04 — Neo4j Graph Schema

## Graph design goals

The graph should make relationships explainable, not merely searchable. Each edge must have a relation type and a source/quality label.

## Node labels

```cypher
(:VehicleMake {name, normalized_name})
(:VehicleModel {name, normalized_name})
(:ModelYear {year, vehicle_id})
(:Component {name, normalized_name, category})
(:Complaint {id, odi_number, received_date, flags})
(:Recall {id, campaign_number, report_received_date})
(:Investigation {id, investigation_number, status, open_date, close_date})
(:ManufacturerCommunication {id, communication_number, communication_type, date})
(:DefectTheme {name, method})
(:Consequence {text_hash, text})
(:Remedy {text_hash, text})
(:DocumentChunk {id, source_type, source_id, field_name, chunk_index})
```

## Relationship types

```cypher
(:VehicleMake)-[:HAS_MODEL]->(:VehicleModel)
(:VehicleModel)-[:HAS_YEAR]->(:ModelYear)
(:ModelYear)-[:HAS_COMPLAINT]->(:Complaint)
(:Complaint)-[:MENTIONS_COMPONENT]->(:Component)
(:Complaint)-[:HAS_THEME {method, confidence}]->(:DefectTheme)

(:Recall)-[:AFFECTS {relation_source}]->(:ModelYear)
(:Recall)-[:RELATED_TO_COMPONENT {relation_source}]->(:Component)
(:Recall)-[:HAS_CONSEQUENCE]->(:Consequence)
(:Recall)-[:HAS_REMEDY]->(:Remedy)

(:Investigation)-[:INVOLVES_MODEL_YEAR {relation_source}]->(:ModelYear)
(:Investigation)-[:INVESTIGATES_COMPONENT {relation_source}]->(:Component)

(:ManufacturerCommunication)-[:INVOLVES_MODEL_YEAR {relation_source}]->(:ModelYear)
(:ManufacturerCommunication)-[:RELATED_TO_COMPONENT {relation_source}]->(:Component)

(:DocumentChunk)-[:EVIDENCE_FOR]->(:Complaint)
(:DocumentChunk)-[:EVIDENCE_FOR]->(:Recall)
(:DocumentChunk)-[:EVIDENCE_FOR]->(:Investigation)
(:DocumentChunk)-[:EVIDENCE_FOR]->(:ManufacturerCommunication)
```

## Edge provenance

Every non-trivial edge should include provenance metadata.

```text
relation_source:
  source_record       # explicit from source row
  normalized_join     # linked by normalized make/model/year/component
  semantic_similarity # linked by retrieval score
  manual_eval_gold    # gold relation in eval set

confidence:
  0.0–1.0 when inferred

created_by:
  ingestion | normalization | retriever | eval_seed
```

## Important semantic distinction

### Official relationship

Use when the source record explicitly contains the affected vehicle/component/campaign.

```text
Recall 22Vxxx AFFECTS Ford F-150 2022
relation_source = source_record
```

### Potential relationship

Use when the system links complaint and recall through make/model/year/component and text similarity.

```text
Complaint cluster POTENTIALLY_RELATED_TO Recall 22Vxxx
relation_source = semantic_similarity
```

Never present a potential relation as official causality.

## Example graph path

```text
Ford
→ HAS_MODEL
F-150
→ HAS_YEAR
2022
→ HAS_COMPLAINT
Complaint ODI #...
→ MENTIONS_COMPONENT
SERVICE BRAKES
← RELATED_TO_COMPONENT
Recall 22V...
→ HAS_REMEDY
Dealer software update / part replacement
```

## Graph build stages

### Stage 1 — deterministic graph

Build only exact source-record and normalized joins.

### Stage 2 — semantic graph enrichment

Add `DefectTheme` nodes and potential complaint-recall/investigation links using embeddings and clustering.

### Stage 3 — evaluation-aware graph

Add curated gold links for eval questions, clearly separated by `relation_source = manual_eval_gold`.

## Retrieval queries

### Vehicle neighborhood

```cypher
MATCH (make:VehicleMake {normalized_name: $make})-[:HAS_MODEL]->(model:VehicleModel {normalized_name: $model})-[:HAS_YEAR]->(year:ModelYear {year: $year})
OPTIONAL MATCH (year)-[:HAS_COMPLAINT]->(c:Complaint)-[:MENTIONS_COMPONENT]->(comp:Component)
OPTIONAL MATCH (r:Recall)-[:AFFECTS]->(year)
OPTIONAL MATCH (r)-[:RELATED_TO_COMPONENT]->(comp)
RETURN year, collect(DISTINCT comp), collect(DISTINCT r), count(DISTINCT c) AS complaint_count
```

### Component evidence neighborhood

```cypher
MATCH (year:ModelYear {vehicle_id: $vehicle_id})
MATCH (comp:Component {normalized_name: $component})
OPTIONAL MATCH (year)-[:HAS_COMPLAINT]->(c:Complaint)-[:MENTIONS_COMPONENT]->(comp)
OPTIONAL MATCH (r:Recall)-[:AFFECTS]->(year)-[:HAS_COMPLAINT]->(:Complaint)-[:MENTIONS_COMPONENT]->(comp)
OPTIONAL MATCH (i:Investigation)-[:INVOLVES_MODEL_YEAR]->(year)
OPTIONAL MATCH (i)-[:INVESTIGATES_COMPONENT]->(comp)
RETURN year, comp, collect(DISTINCT c), collect(DISTINCT r), collect(DISTINCT i)
```
