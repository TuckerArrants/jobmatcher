# jobmatcher

Collects job postings from free public job boards, drops clear mismatches in code, classifies
the rest as **apply / consider / skip** for one person, and delivers a ranked digest. How well the
classifier agrees with your own judgement is measured on a held-out set of your labels.

> Status: under construction. Ingest and the prefilter work; the classifier, report and setup
> wizard are next. The quickstart below describes the finished tool.

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

```powershell
git clone https://github.com/YOU/YOUR-PRIVATE-COPY.git
cd YOUR-PRIVATE-COPY
python -m venv .venv
.venv\Scripts\activate          # macOS / Linux: source .venv/bin/activate
pip install -e .
copy env.example .env           # macOS / Linux: cp env.example .env ; then put your key in .env
jobmatch run
```

`.env` is git-ignored and blocked by the privacy check. Your working database stays in
`user/data/` on your machine. Commit and push `user/` whenever you want a backup of your
reports and labels.

## What runs where

| step | how | cost |
|---|---|---|
| ingest | public Greenhouse, Lever and Ashby board APIs, one request per board per run | free |
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

## Prefilter settings

Defaults are in `src/jobmatcher/config/filters.default.yaml`; override any key in
`user/filters.yaml`. Location matching is offline: set `home_zip` and `radius_miles` to keep
onsite and hybrid postings within that distance, plus remote ones. Postings whose location, date
or salary cannot be read are kept, not dropped.

Find a company's board with `jobmatch find-board "Company Name"`.

## Licence

MIT
