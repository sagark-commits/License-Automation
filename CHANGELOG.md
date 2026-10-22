# Changelog

## Web UI + correctness fixes
- New browser UI (`run_web.py`, package `lic_web/`) to generate reports, manage tenants/agent types, databases and users. See HOWTO_WEB_UI.md.
- New `verify_report.py`: re-reads both workbooks and reconciles them; run automatically after every UI report.
- Fix: wallboard login sheets were empty for tenants without a configured wallboard (login query joined campaign rows wallboard users do not have).
- Fix: missing supervisor login sheets for MBSA ARC-1, KPWKM, Gamuda; any licence with a peak now gets a login sheet automatically.
- Fix: Agent + Executive (several user types in one licence) are summed per hour before taking the peak.
- Fix: per-tenant user-type lists now replace the defaults exactly (no hidden aliases); unmapped user types are reported, not silently dropped.
- Fix: login-query failures are reported instead of producing empty sheets; DB connections are read-only with a statement timeout.
- Wallboard users are detected for every tenant. New optional per-tenant `supervisors_without_campaign` (off by default).
- Repo hygiene: UTF-16/BOM files converted to UTF-8 (`.bat` CRLF), `.gitattributes` added.
