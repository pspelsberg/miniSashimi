---
name: code-security-loop-fix
description: >
  Autonomer Workflow- und Schleifen-Skill zum automatischen Identifizieren, Planen,
  Beheben und Rezensieren von kritischen Sicherheitsfehlern und Warnungen im
  Quellcode unter Verwendung des Skills 'code-security-review'. Schließt mit
  einem detaillierten Bericht ab.
version: 1.0
updated: 2026-06-20
---

# Code-Security-Loop-Fix: Autonomer Behebungs-Workflow

Du bist ein autonomer Security-Agent, der Sicherheitslücken in einer kontrollierten Schleife identifiziert, behebt und validiert. Dein Ziel ist es, kritische Befunde und Warnungen vollständig zu eliminieren und den Prozess detailliert in einem Report zu dokumentieren.

---

## 🛠️ Arbeitsanweisungen & Workflow

Befolge exakt den folgenden Algorithmus, wenn dieser Skill aufgerufen wird:

### Schritt 1: Initiale Analyse & RAG-Abfrage
1. Aktiviere den Skill `code-review-security` (ausgeführt auf dem Ziel-Quellcode).
2. Analysiere den Code und erfasse alle Befunde – insbesondere den maschinenlesbaren JSON-Findings-Block am Ende der Ausgabe.
3. **LanceDB RAG-Abfrage:** Falls sicherheitsrelevante Befunde (CWEs) vorliegen, frage die lokale LanceDB-Fehlerdatenbank nach bekannten Lösungen für ähnliche Fehler ab:
   ```bash
   python3 .agents/skills/error_healing_memory/scripts/query_db.py --query "<CWE-ID oder Fehlertyp>"
   ```
   Nutze gefundene Lösungsansätze als Referenz für die Behebungsplanung.
4. Initialisiere den Schleifenzähler: `iteration_count = 1`.

### Schritt 2: Filterung & Behebungsplanung
1. Filtere alle Befunde aus der aktuellen Iteration. Relevante Schweregrade für die Behebung sind:
   - `KRITISCH` (entspricht `Critical`)
   - `WARNUNG` (entspricht `Warning`)
2. **Bedingungsprüfung**:
   - **Erfolgspfad**: Wenn **keine** Befunde mit Schweregrad `KRITISCH` oder `WARNUNG` (mehr) existieren:
     - Gehe direkt zu **Schritt 5 (Berichterstattung)** mit dem Status `"Erfolgreich bereinigt"`.
   - **Abbruchpfad**: Wenn noch Fehler existieren, aber das Limit von **5 Iterationen** erreicht ist (d. h. `iteration_count > 5` nach Inkrementierung, oder du stehst vor der 6. Iteration):
     - Gehe direkt zu **Schritt 5 (Berichterstattung)** mit dem Status `"Teilweise behoben - Limit erreicht"`.
   - **Fortsetzungspfad**: Andernfalls erstelle einen dedizierten, sequenziellen Code-Fix-Plan für alle verbleibenden kritischen Fehler und Warnungen dieser Iteration.

### Schritt 3: Automatisierte Code-Behebung (Fix) & Lernen
1. Wende die im Behebungsplan definierten Code-Änderungen präzise auf die betroffenen Quelldateien an.
2. Nutze entsprechende Bearbeitungs-Tools (`replace_file_content` or `multi_replace_file_content`).
3. **Syntax-Validierung**: Führe nach den Änderungen eine Validierung durch (z. B. durch Ausführen eines Linters, Compilers oder Tests im Repository), um Folgefehler oder unvollständige Codeblöcke zu verhindern.
4. Protokolliere den durchgeführten Fix (Datei, Zeile, CWE, Beschreibung) für die spätere Berichterstattung.
5. **LanceDB-Dokumentation (Lernen):** Nach erfolgreichem Fix und erfolgreicher Validierung trage die gefundene Lösung in das LanceDB-Gedächtnis ein, damit zukünftige Sitzungen davon profitieren können:
   ```bash
   python3 .agents/skills/error_healing_memory/scripts/query_db.py --add \
     --task "Fix für [CWE-ID / Fehlerbeschreibung]" \
     --error "[Originale Fehlermeldung aus der Sicherheitsanalyse]" \
     --solution "[JSON-String oder detaillierte Beschreibung der vorgenommenen Änderungen]"
   ```
6. Inkrementiere den internen Schleifenzähler: `iteration_count++`.

### Schritt 4: Validierungsschleife (Re-Review)
1. Führe den Skill `code-review-security` erneut auf dem modifizierten Quellcode aus.
2. Gehe zurück zu **Schritt 2**.

---

## 📝 Spezifikation des Sicherheitsberichts (`security_report.md`)

Nach Abschluss der Schleife (Schritt 5) musst du einen detaillierten Bericht im Hauptverzeichnis des Projekts bzw. im aktuellen Arbeitsverzeichnis als `security_report.md` erstellen. Verwende exakt die folgende Struktur:

```markdown
# Security Review & Fix Report

- **Status:** [Zusammenfassender Status, z. B. "Erfolgreich bereinigt" oder "Teilweise behoben - Limit erreicht"]
- **Iterationen:** [Anzahl der benötigten/durchlaufenen Iterationen (z. B. 3 von 5)]

## Historie der Iterationen
[Chronologische Liste aller Durchläufe. Gib für jede Iteration an, wie viele kritische Fehler/Warnungen gefunden und wie viele davon behoben wurden.]

* **Iteration 1**: X Befunde gefunden (Y Kritisch, Z Warnung) -> Y+Z behoben.
* **Iteration 2**: ...

## Details der Behebungen (Fixes)

| Datei | Zeile | Schwachstelle (CWE) | Beschreibung des Fixes |
| :--- | :--- | :--- | :--- |
| [Dateipfad/Dateiname](file:///absoluter/pfad/zur/datei) | [Zeile(n)] | [CWE-ID] | [Kurze Beschreibung, wie der Fehler behoben wurde] |

## Verbliebene Risiken & Hinweise
* **Ignorierte Meldungen (Low/Info/Optimierung):**
  - [Hier alle Optimierungen oder Guter Standard-Meldungen auflisten, die ignoriert wurden]
* **Nicht behebbare Fehler (falls Limit erreicht):**
  - [Hier verbliebene kritische Fehler oder Warnungen auflisten, falls die Schleife bei Iteration 5 abgebrochen wurde]
```

> [!IMPORTANT]
> Verlinke alle Dateien in der Details-Tabelle mit vollqualifizierten Markdown-Links (`file:///...`) und gib präzise Zeilennummern an. Stelle sicher, dass die Tabelle sauber formatiert ist.
