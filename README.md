# jobmatcher

A vibe coded weekend project. Collects job postings from free public job boards, drops clear mismatches in code, classifies
the rest as **apply / consider / skip** for one person, and delivers a ranked digest. How well the
classifier agrees with your own judgement is measured on a held-out set of your labels.

> Status: under construction. Ingest, the prefilter and the classifier work; the daily report,
> setup wizard and evaluation are next. The quickstart below describes the finished tool.

## Quickstart

1. On GitHub, click **Use this template** and create a **private** repository.
   **Do not fork.** A fork of a public repository is public and cannot be made private, and your
   profile would be visible to everyone.
2. Clone your private copy, then run `pip install -e .` and `jobmatch init`. Or copy
   `user.example/` to `user/` and edit the files by hand.
3. Add the repository secret `TYPESAFE_API_KEY`.
4. Enable the scheduled workflow. The first digest arrives on the next run.

### Or run it on your own computer only

If you would rather your key never touch GitHub, skip steps 3 and 4. The scheduled workflow does
nothing without the secret. Instead:

**Windows, no command line:**
1. Install Python from python.org (tick *Add python.exe to PATH*) and GitHub Desktop.
2. In GitHub Desktop, sign in, then use *File → Clone repository* to clone your private copy.
3. Click *Show in Explorer*, then double-click `windows-setup.bat`. It installs everything and
   opens Notepad so you can paste your key.
4. Double-click `windows-run.bat` whenever you want a run.

To get updates, click *Fetch origin*, then *Pull origin* in GitHub Desktop. If an update adds a
new dependency, double-click `windows-setup.bat` again; this is safe and keeps your key.

**macOS / Linux:**

```bash
git clone https://github.com/YOU/YOUR-PRIVATE-COPY.git
cd YOUR-PRIVATE-COPY
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp env.example .env             # then put your key in .env
jobmatch run
```

`.env` is git-ignored and blocked by the privacy check. Your working database stays in
`user/data/` on your machine. Commit and push `user/` whenever you want a backup of your
reports and labels.

## What runs where

| step | how | cost |
|---|---|---|
| ingest | public Greenhouse, Lever and Ashby board APIs, one request per board per run | free |
| ingest (optional) | keyword searches on Adzuna and USAJobs (`user/searches.yaml`), when their free keys are in `.env` | free |
| prefilter | code only: age, title words, seniority, location and radius, clearance, years required, salary floor | free |
| classify | one call per new posting to the classifier (baseline: TypeSafe Jev) | about $0.17 per 1,000 postings |
| decide, report | code | free |

No scraping of LinkedIn, Indeed, Glassdoor or any other site whose terms forbid it.

## Your data

* Everything personal lives in one folder, `user/`. The public repository ships only
  `user.example/`, a fictional person.
* The only outside party that receives anything personal is the classifier's API: each posting's
  text plus your profile. Nothing else leaves the machine or the GitHub runner.
* Workflow logs print counts and costs only, never titles, companies, scores or labels.
* `jobmatch privacy-check` (also a pre-commit hook and a CI step) fails if personal files,
  databases or keys are tracked. Enable the hook with `git config core.hooksPath .githooks`.

## Commands

| command | what it does |
|---|---|
| `jobmatch run` | ingest, then classify the new postings that passed the prefilter |
| `jobmatch ingest` | fetch every board once, store new postings, apply the prefilter |
| `jobmatch classify --dry-run` | show how many postings would be sent and the estimated cost; send nothing. The first request body is written to `user/cache/dry-run-example.json` |
| `jobmatch classify --limit 100` | classify at most 100 postings, newest first |
| `jobmatch find-board "Name"` | find a company's public board; `--file names.txt --write` adds many to `companies.yaml` |
| `jobmatch privacy-check` | fail if personal data, databases or keys are tracked |

`classify` and `run` refuse to start when the estimated cost is above `--max-cost` (default
$1.00). Every answer is cached, so a posting is never paid for twice. Editing your profile makes
earlier answers stale, so those postings are asked again.

## The classifier

The baseline is TypeSafe's Jev (`jev-1.13.0`, pinned), a zero-shot model that returns
probabilities, not text. The questions are in `src/jobmatcher/config/questions/jobs_v1.yaml`.
They cover role match, domain interest, hard blockers, unwanted work, level fit and an overall
verdict. Choice questions are asked twice, once with the options reversed, because Jev favours
the first option. The decision rules that turn answers into apply / consider / skip run in code,
from `src/jobmatcher/config/decision.default.yaml`. When the two orderings disagree, the posting
is labelled *consider*, never guessed.

## Sources

* **Company boards** (`user/companies.yaml`): full posting text, but only the employers you list
  that use Greenhouse, Lever or Ashby.
* **Adzuna** (optional): a real job search, so it also finds employers on Workday and other
  systems, and local ones. It returns only a description snippet, and its predicted salaries are
  ignored. Listings come from Adzuna under its terms, so the report credits *Jobs by Adzuna*, and
  the raw data stays in your private copy. Get keys at developer.adzuna.com.
* **USAJobs** (optional): federal jobs with full text. Jobs open only to current federal
  employees and closed announcements are skipped. Get a key at developer.usajobs.gov; your email
  is sent as the User-Agent header, as USAJobs requires.

When the same job appears in several sources (same company and title), the copy with the fullest
text is kept and the others are marked `duplicate`.

## Prefilter settings

Defaults are in `src/jobmatcher/config/filters.default.yaml`; override any key in
`user/filters.yaml`. Location matching is offline: set `home_zip` and `radius_miles` to keep
onsite and hybrid postings within that distance, plus remote ones. Postings whose location, date
or salary cannot be read are kept, not dropped.

Find a company's board with `jobmatch find-board "Company Name"`.

## Licence

MIT
