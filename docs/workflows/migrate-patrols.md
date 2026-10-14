# Migrate SMART patrols

The patrol migration moves a SMART conservation area's patrol setup and
history into EarthRanger's patrol model from a backup or export zip, without
SMART Connect. This page covers the first step, `patrol-datamodel`; the
readiness check and the history import follow in later releases.

1. Get the site's SMART backup or Conservation Area export zip and run
   `./smart-able all <zip>` in
   [smart-able](https://github.com/PADAS/er-smart-able). This writes
   `work/export/<ca_id>/datamodel.xml`, the configurable models and
   `work/export/<ca_id>/bundle/`.
2. Push the event data model:
   `er-smart-sync datamodel --from-file work/export/<ca_id>/datamodel.xml --cm-from-file work/export/<ca_id>/configurable_model_<uuid>.xml --ca-identifier <ID> ...`
   (see [Push a data model](push-datamodel.md)).
3. Pick the label language in smart-able's Data Model tab and note the
   timezone the bundle was written for (`manifest.json`); set
   `smart.use_language_code` and `smart.timezone` in `sync.yaml`, and
   `bundle.path` to the bundle directory.
4. Run `er-smart-sync --dry-run patrol-datamodel --config sync.yaml`, read
   the plan, then run it without `--dry-run`. See
   [patrol-datamodel](../cli-reference/patrol-datamodel.md). The run saves
   the site's previous universal-fields document next to the checklist;
   that page's "Undoing a run" section says how to remove everything the
   command created, should a site need it.
5. Work through `checklist.md`: enable the `patrol_schemas` preview feature
   if it is off (then re-run step 4 so the universal patrol fields are
   written), create the teams it lists, and put the subjects in the roster
   group if the tool could not.
6. Run `er-smart-sync patrol-preflight --config sync.yaml` and fix what it
   reports until it shows no blocking findings. See
   [patrol-preflight](../cli-reference/patrol-preflight.md).
7. `import-patrols` follows in a later release.

The universal patrol fields need the `patrol_schemas` preview feature; on a
site where it is off the command still writes the patrol types, choices and
subjects and puts the feature on the checklist (seen on gundi-dev before it
was enabled there).

Quirks of the SMART data that matter here (zone-less times, placeholder
dates, retired staff) are written up in smart-able's `quirks.html`,
published with every browser it builds.
