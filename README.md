# Review Miner

Turn your competitors' 1★ and 2★ reviews into leads.

Review Miner reads the negative reviews of your competitors on G2, Capterra, TrustRadius and Trustpilot. It keeps the reviewers who are on the buying side, finds who they really are on LinkedIn, checks the match, and looks up their work email. Everything lands in a leads file for a human to review. **It never sends anything.**

## How it works

| Stage | Script | What it does |
|---|---|---|
| 1. Competitors | `configs/<slug>.json` | You list the competitors and their review pages (do this in chat with an AI assistant, not by hand) |
| 2. Scrape | `1_scrape.py` | Pulls every 1★ and 2★ review since your start date. Each site needs its own approach, and two scrapers back each other up. If any source fails, the run stops and the last complete run is kept |
| 3. Filter + find | `2_candidates.py` | Anonymous reviews are skipped. G2, Capterra and TrustRadius give a job title. Trustpilot doesn't, so an LLM keeps only buyer-side reviewers there, and only with a quote from the review as proof (set `buyer_hint` in the config to describe your buyer). It then finds LinkedIn candidates: people search from the review details (name, title, industry, location, size), Google Lens on the review photo, and a Google search as the last fallback |
| 4. Verify | `3_verify.py` | Name (nicknames count), same picture (pixel comparison, not face recognition), title, and company size as a tie-break. Every match gets a status |
| 5. Email | `4_enrich.py` | Name, LinkedIn URL and company domain go into one email lookup backed by a provider waterfall |

Helpers: `lib.py` (API and LLM calls, with caching), `store.py` (JSON files under `data/`), `parse.py` (review page parsers), `match.py` (name and photo matching), `test_parse.py` (offline tests).

### Confidence

| Status | Meaning |
|---|---|
| `confirmed` | Same picture on the review and LinkedIn, or the review site linked the profile |
| `probable` | One name match whose title fits and whose company doesn't contradict the review. A human checks it |
| `guess` | Best name match only. Often the wrong person. Never contact without checking |

## Setup

Needs Python 3.12+ and accounts on [treg.to](https://treg.to) (all data calls) and [OpenRouter](https://openrouter.ai) (the LLM).

```bash
git clone https://github.com/salmanbareesh/review-miner.git
cd review-miner
python3 -m pip install -r requirements.txt
cp .env.example .env                              # add TREG_TOKEN and OPENROUTER_API_KEY
cp config.example.json configs/acme.json          # add your competitors
python3 scripts/test_parse.py                     # offline check, should print "ok"
```

## Run

```bash
COMPANY=acme python3 scripts/1_scrape.py       # -> data/acme/reviews.json
COMPANY=acme python3 scripts/2_candidates.py   # -> data/acme/candidates.json
COMPANY=acme python3 scripts/3_verify.py       # -> data/acme/matches.json
COMPANY=acme python3 scripts/4_enrich.py       # -> data/acme/leads.json
```

Each stage reads the previous stage's file, so you can re-run one stage without the others. Paid answers are cached in `data/acme/`, so a re-run doesn't pay twice for the same call.

`ONLY_SITES=trustpilot` re-reads only the listed sites and keeps the rest. `ALLOW_FAILED=1` lets a run with a failed source write its output anyway.

Run it every 2 weeks if your competitors get lots of reviews, once a month if they're quieter.

## When it won't work

It needs volume. If your competitors have ten reviews between them, you'll get almost nothing. Count the 1★ and 2★ reviews on their G2 and Capterra pages before you set it up.

## Run it as a Grok Bot

See [GROKBOT.md](GROKBOT.md): paste one message and Grok Bot clones this repo, sets it up, and runs it on a schedule.

## Rules

- Nothing is ever sent. A person reviews every lead.
- Reviewer data is personal data. Use it for one-to-one outreach that respects the review sites' terms and your local laws, and don't publish it.
