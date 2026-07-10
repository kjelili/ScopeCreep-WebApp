# User Guide — for project professionals testing the app

You need a running instance (see README quick start) and a browser. Phone,
tablet or desktop all work; the interface adapts to each.

## The two-minute tour (no setup, no cost)

1. Open the app and click **Launch the app** (or go straight to `/app.html`).
2. Click **Load sample project** in the top bar. A hospital-project scope
   document (28 sections) and 25 test emails load automatically.
3. Press **Analyse emails**. The progress bar fills; on the sample this
   takes about a second in demo mode.
4. Explore the results: summary cards, a risk-distribution bar, and a list
   of every email sorted by verdict and risk. Tap any row to open the
   detail drawer — justification, suggested action, the cited scope clause
   with its verification status, and the exact scope sections the system
   compared against.
5. Try the filter chips (**Flagged**, **High & Extreme**, **Unverified
   evidence**, **Alerted**) and the search box.
6. Click **Export CSV** to download the full analysis for your records.

Demo mode uses a deterministic offline heuristic and is watermarked as
such. It exists so you can evaluate the *workflow*; judge the *judgements*
in OpenAI mode.

## Using your own project

1. **Scope baseline** — upload the document that defines what is in scope:
   a scope statement, employer's requirements, or the relevant contract
   schedule. PDF, DOCX or TXT. Scanned PDFs need OCR first.
2. **Emails** — export project emails to CSV with a column named
   `email_body` (one email per row). Any other columns (sender, date,
   subject) are kept and appear in your exported results.
3. **Engine** — switch to **OpenAI — live AI** and paste an API key. The
   key is used for this run only; it is not stored, logged or echoed.
4. **Alert threshold** — choose when an email counts as alert-worthy:
   Moderate+, High+ (default) or Extreme only. Start at High+; if you find
   yourself wanting more sensitivity, drop to Moderate+ and watch whether
   the extra alerts earn their keep.
5. **SMS recipients** — optional. Requires Twilio credentials on the
   server (see README). Alerts are sent once per email per recipient and
   contain no email content.

## The privacy scrub (step 2)

Your email CSV is parsed on your device, not uploaded. Before analysis,
the app replaces personal information — names in greetings and sign-offs,
email addresses, phone numbers, URLs, postcodes — with consistent
pseudonyms like [PERSON-1]. Add your project's names (people, companies,
sites) to the dictionary so they are redacted too; patterns cannot know
them. Review the highlighted preview, then approve. Only the anonymised
text is uploaded and analysed.

Because pseudonyms are consistent, the analysis is unaffected in practice:
"[PERSON-1] asked us to add extra sockets" is flagged exactly as the
original would be. After the run, the results screen shows real names
again — that re-identification happens only on your device, using a
mapping that was never uploaded. A toggle lets you see exactly what the
server saw. Exports keep the pseudonyms, so the CSV on your disk is as
anonymous as what was analysed.

You can switch anonymisation off, but you will be asked to confirm.

## Reading a result

- **Scope creep / In scope** — the verdict for that email.
- **Risk** — Low, Moderate, High or Extreme.
- **Evidence: verified / unverified** — whether the scope clause the AI
  cited was independently confirmed to exist in your document. Treat
  *unverified* flags with suspicion: the reasoning may still be right, but
  the quoted evidence could not be matched to the baseline.
- **Low retrieval relevance** (in the drawer) — the email didn't match any
  scope section well; the judgement rests on weak context.

## Reviewing as the project manager

Every result can carry your judgement alongside the AI's. Open a row and
use the review section at the bottom of the drawer: confirm or overturn
the verdict, set the risk level you would assign, say whether the cited
evidence is correct, and add a note. Saved reviews stay on your device,
show a "PM ✓" badge in the list, and appear in the CSV export as separate
pm_* columns — the AI's original answers are never modified, so the export
records both opinions side by side. The "Unreviewed" filter shows what is
left to check.

Evidence badges come in three states: **verified** (the AI quoted a real
clause the email conflicts with), **boundary ✓** (the request is absent
from scope and the AI quoted the clause defining the boundary it falls
outside), and **unverified / no citation** (treat with more care).

## Cumulative drift, evidence packs and your measured precision

Scope creep rarely arrives as one big request — it accumulates. Below the
results list, the drift section groups your emails into threads (add
`subject`, `thread` or `date` columns to your CSV for better grouping) and
shows each as a row of dots — one per email, coloured by risk. Threads
with two or more flags receive an aggregate judgement: whether the
sequence, taken together, amounts to a material drift, at what risk, and
what single governance action to take next. Click any dot to open that
email.

From any email's drawer, **Evidence pack** downloads a printable document
— the anonymised excerpt, the cited clause with its verification state,
the AI's assessment, your review and the thread context — formatted for
submission into your organisation's existing change-control process. The
tool deliberately stops there: it prepares the referral; your process
decides.

Once you have reviewed five or more flagged emails, a **measured
precision** card appears: the percentage of the AI's flags that you, the
reviewer, confirmed. No vendor promise — your own measurement, from your
own judgements, on your own project.

## Things worth knowing

- Results live in memory: export the CSV before stopping the server.
- Cancelling a run keeps the rows analysed so far.
- The tool recommends; it never acts. Every flag is a prompt for a human
  decision, by design.
