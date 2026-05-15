# Event-type version (v1 vs v2)

EarthRanger supports two coexisting event-type API versions. `er-smart-sync`
defaults to **v2**; v1 is supported for tenants that haven't migrated yet.
This page explains the differences and when to use each.

## Quick comparison

| Aspect | v1 | v2 |
|---|---|---|
| Endpoint | `/api/v1.0/activity/events/eventtypes/` | `/api/v2.0/activity/eventtypes/` |
| `schema` field | stringified JSON | JSON object (`{json: ..., ui: ...}`) |
| Schema spec | draft-04 + Jinja2 templates | **JSON Schema 2020-12**, strict |
| `category` field | UUID FK | category `value` slug |
| Choice attributes | inline `enum` in schema | `$ref` to separate `Choice` records |
| `readonly` | nested in schema | top-level field |
| UI metadata | n/a | dedicated `ui` block with sections, fields, parents |
| Inactive event types | soft-delete via DELETE | hard-delete (409 if in use) |

## When to use which

**Default: v2.** Use it unless you have a specific reason not to.

**Use v1 only if:**

- Your EarthRanger tenant hasn't enabled v2 yet (rare; check with the ER team).
- You're maintaining a legacy integration that downstream consumers expect to
  see in v1 shape.
- You're testing migration scenarios.

**Set the version per-run with the CLI flag** or per-config with the YAML:

```yaml
earthranger:
  event_type_version: v2   # or v1
```

```bash
er-smart-sync datamodel --event-type-version v1 ...
```

CLI flag overrides config.

## What `er-smart-sync` produces

### v2 schema shape

For every event type, `er-smart-sync` emits a meta-schema-valid v2 schema:

```json
{
  "schema": {
    "json": {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "type": "object",
      "unevaluatedProperties": false,
      "properties": {
        "sector": {
          "type": "array",
          "title": "Sector",
          "description": "",
          "deprecated": false,
          "uniqueItems": true,
          "items": {
            "type": "string",
            "anyOf": [{"$ref": "/api/v2.0/schemas/choices.json?field=et5e6b96f4_sector"}]
          }
        }
      },
      "required": []
    },
    "ui": {
      "fields": {"sector": {"type": "CHOICE_LIST", "inputType": "DROPDOWN", "parent": "section-1", ...}},
      "headers": {},
      "order": ["section-1"],
      "sections": {"section-1": {"columns": 1, "isActive": true, "leftColumn": [...], "rightColumn": []}}
    }
  },
  "category": "jkperu",
  "readonly": false
}
```

Note: choices are referenced via `$ref`. The referenced records are created
by the choices phase before the event type is POSTed.

### v1 schema shape

Legacy form, simpler but less expressive:

```json
{
  "schema": "{\"schema\": {\"properties\": {\"sector\": {\"type\": \"array\", \"enum\": [\"riootros\", \"jk001\", ...]}}}}",
  "category": "<UUID>",
  "is_active": true
}
```

Choices are inlined in the schema. The `category` field is the UUID FK to the
event category, not the slug.

![ER event-type detail view (v2)](../images/er-event-type-form-v2.png)
*An EarthRanger v2 event-type form, showing the `ui.sections` layout and a
populated CHOICE_LIST field.*

!!! note "Screenshot placeholder"
    Replace with a screenshot of the EarthRanger admin → Event Types →
    detail view for a v2 event type. See `docs/images/README.md`.

## Migrating between versions

EarthRanger's event-type `value` is unique tenant-wide **across both
versions**. You can't have the same `value` exist as both v1 and v2.

To convert an existing v1 event type to v2, use the EarthRanger server-side
migrate endpoint:

```
POST /api/v2.0/activity/eventtypes/migrate/
```

`er-smart-sync` doesn't wrap this endpoint yet. Call it directly via `curl`
or the EarthRanger admin UI.

## Related

- [Workflow: Push a data model](../workflows/push-datamodel.md)
- [Concept: Choices](choices.md) — the v2 `$ref` mechanism in detail
- [Configuration](../getting-started/config.md) — setting `event_type_version`
