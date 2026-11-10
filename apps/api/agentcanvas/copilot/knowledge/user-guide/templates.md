# Template gallery

On **Home**, under **…or start from a template**, the gallery holds 38 ready-to-run workflows for common jobs, at
three levels: **beginner**, **intermediate** and **advanced**. Search by name, description or tag (for example
`email`, `sql` or `approval`), pick a level, or click a category chip to narrow the list. Each card shows the
category, a short description, tags, how many blocks the workflow has, its level and what else it adds to your
project (below). Clicking a card (or focusing it and pressing **Enter**) creates the workflow, named after the
template unless you typed a name, and opens it in the editor.

Every block in the palette, and every feature that lives outside the canvas, is shown by at least one template, so
when you want to see how something is used, there is a template that uses it.

## What a template adds beside the workflow

Some templates bring more than the workflow. The card says what ("Also adds 3 workspace files, a dataset of 4
examples, 2 evaluators, 2 saved tests"), and a message after you create it confirms what was added:

- **Workspace files**: an event handler (`events/`), a script (`scripts/`), a custom block (`nodes/`) or Python
  tools (`tools/`), written to your [Workspace](workspace.md). A file you already have is kept as it is.
- **An evaluation dataset** with **evaluators**, ready for an experiment in the [Evaluation Studio](evaluation.md).
- **Saved tests**, in the editor's **Tests** tab.
- **A schedule**, under **Versions › Triggers**. It is created switched off; turn it on when you are ready.

Templates use the model you chose in first-run setup. Every template validates with no problems and runs end to end
on the demo model, so you can press **▶ Run** straight away; the run input is pre-filled with the workflow's input
fields. Templates that pause for a person (an approval or a tool call) show the question in the Run tab.

!!! tip "Make it yours"
    Templates are starting points. Replace sample text in prompts (the FAQ in **Customer FAQ bot**, the handbook in
    **Onboarding buddy**) with your own, swap models in the Inspector, and add steps from the palette.

## Getting started

| Template | What it does | Level |
|---|---|---|
| **Approval workflow** | An LLM drafts, a human approves (or edits) the draft, and only approved drafts are published. | beginner |
| **Chat assistant** | A single tool-less agent that chats over the `messages` channel. Start here and add tools. | beginner |
| **Classify and route** | An LLM classifies the input, a router picks a branch, each branch writes a reply. | beginner |
| **AI triage with a confidence check** | An AI decides whether a message is a question or a complaint, says how sure it is and why, and sends anything it is unsure about to a person. | intermediate |

## Customer support

| Template | What it does | Level |
|---|---|---|
| **Email reply with approval** | Drafts a reply to an email in the tone you choose; a person approves or edits it before it is marked ready to send. | beginner |
| **Customer FAQ bot** | A support chatbot that answers only from the FAQ in its instructions and hands anything else to a person. Replace the sample FAQ with your own. | beginner |
| **Feedback sentiment triage** | Reads a piece of customer feedback, labels its sentiment and routes it: negative feedback gets a careful drafted reply, positive feedback a thank-you. | beginner |
| **Support triage** | Classify a ticket's urgency; urgent tickets are escalated and wait for a manager, the rest get a drafted reply. | intermediate |
| **Memory concierge** | Remembers each guest's preferences from one conversation to the next, uses them when it answers, and deletes them all when the guest says "forget me". | intermediate |
| **Support inbox from email events** | Each email event starts a run: a prompt-injection check, personal data hidden, your workspace code tidies the email, an AI sorts it and drafts a reply; a person adds a missing order number and urgent replies wait for a manager. Adds an event handler, a script, a dataset, evaluators and tests. | advanced |

## Content

| Template | What it does | Level |
|---|---|---|
| **Blog post writer** | Outline, then draft, then an editor's review: a rejected draft is revised and comes back for review until the editor approves it. | intermediate |
| **Social media posts** | Turns one announcement into a LinkedIn post, a post for X and an Instagram caption, one step per network. | beginner |
| **Translation with glossary** | Translates text into another language using your glossary for brand and product terms, then checks that every glossary term was used. | beginner |
| **Editorial crew** | A CrewAI crew researches and writes an article, an editor agent polishes it under a house rule, and a person approves it. Runs on CrewAI (`make crewai` on the server). | advanced |

## Sales

| Template | What it does | Level |
|---|---|---|
| **Lead qualification** | Scores an inbound lead (need, budget, timeline) as structured output; hot leads get a drafted first email, the rest go to the nurture list. | intermediate |
| **Product description writer** | Writes shop-ready product copy (headline, description, bullet points, SEO keywords) from a list of facts, as structured output. | beginner |
| **Quote with a follow-up** | Drafts a quote, then [waits](widgets.md#wait) up to three days for the customer's reply: send the event `quote.replied` with the customer's id as its `key`. If nobody replies, it writes a friendly reminder. | intermediate |
| **Lead scoring with research** | Your own scoring block rates each lead; for a hot lead an agent looks the company up with Python tools from the workspace and drafts a first email. Adds the block, the tools, a dataset, evaluators and tests. | advanced |

## Data extraction

| Template | What it does | Level |
|---|---|---|
| **Data extraction** | Turn free text (an email, an invoice) into validated structured fields with an agent's structured output. | beginner |
| **Meeting notes and action items** | Turns a meeting transcript into a summary, decisions and action items (owner, task, due date) as structured output, then drafts the follow-up email. | intermediate |

## Research

| Template | What it does | Level |
|---|---|---|
| **Ask your documents** | Answers questions from a knowledge base (Knowledge page) with citations: Search knowledge finds the best passages by words and meaning, then the model answers only from them and cites them as [1], [2]. Load the sample 'handbook' knowledge base to try it, and score its search with the sample retrieval eval set. | beginner |
| **Document Q&A** | Answers a question about a document you paste in (a contract, a manual, a policy): it first quotes the relevant passages, then answers only from them. | beginner |
| **RAG Q&A** | Answer questions from a knowledge base: recall the most relevant passages from memory, answer only from them. Fill the `knowledge` memory space first (Memory Remember node or the Memory page). | intermediate |
| **Research brief** | Map-reduce research: one LLM call per topic in parallel, then a Deep Agent writes the brief and keeps findings in the user's long-term /memories/ folder. | intermediate |
| **Web research agent** | Plans search queries for a question, then an agent researches it with a Wikipedia search tool and writes a sourced answer. Every web call waits for your approval. | intermediate |
| **Resilient research agent** | A research agent built to keep going: a to-do list, long-term memory, shorter long chats, retries and backup models; a writer drafts in scratch files and must pass a quality check. | advanced |
| **Mixed-framework team** | Two agents from two frameworks in one flow: an OpenAI Agents SDK researcher hands its notes to a CrewAI writer, whose typed answer (a title and a summary) is checked where the two meet. Exports as one project that installs each framework. Runs need CrewAI on the server (`make crewai`). | intermediate |

## HR

| Template | What it does | Level |
|---|---|---|
| **Onboarding buddy** | A chat assistant for new hires that answers from your handbook excerpts and never repeats email addresses people paste in. Replace the sample handbook with yours. | beginner |
| **Resume screening** | Compares a resume with a job description (skills and experience only) as structured output; a recruiter approves the recommendation before anything happens. | intermediate |

## Engineering

| Template | What it does | Level |
|---|---|---|
| **Code review assistant** | Reviews a diff from two angles (bugs and security, then readability and tests) and writes one review comment. | intermediate |
| **Incident summary** | Turns raw incident notes (alerts, chat, timeline) into a timeline and a blameless summary for stakeholders; the incident lead approves it before it goes out. | beginner |
| **SQL query helper** | Drafts a SQL query that answers a question about your tables and explains it in plain English. It never connects to a database: you review and run the query yourself. | beginner |
| **Parallel code review panel** | Security, performance and readability reviewers look at a pull request at the same time (a group of steps with parallel branches and a Wait for all), and a lead reviewer writes one verdict. | advanced |
| **Incident agent network** | An incident lead weighs what three agents find (one over A2A, one over MCP, one built with the OpenAI Agents SDK), checks the issue tracker through an app connector and opens an issue after you approve. Replace its placeholder addresses. Not offered on the hosted service. | advanced |

## Operations

| Template | What it does | Level |
|---|---|---|
| **Policy compliance check** | Checks a document against your rules, quoting every passage that breaks one. Anything that is not clearly compliant goes to a reviewer. | intermediate |
| **Travel itinerary planner** | Plans a day-by-day itinerary within your budget from a short trip description, then writes a packing list for it. | beginner |
| **Content safety gate** | Checks a review or forum post for harmful content, turns it into a structured summary and checks the summary's format before it is ready to publish; anything that fails is held for a moderator. | intermediate |
| **Daily metrics report** | Every weekday morning your workspace code gathers yesterday's numbers, an AI writes a structured report, and its format is checked. Adds the script and a schedule (switched off). | intermediate |

## Good to know

- **Human approval.** Approval workflow, Support triage, Email reply with approval, Blog post writer, Resume
  screening, Incident summary and Policy compliance check pause for a person; answer in the Run tab or the
  [Inbox](run-and-debug.md).
- **Waiting.** Quote with a follow-up pauses at a Wait step until the customer's reply event arrives or three days
  pass, then continues on its own ([Waiting runs](run-and-debug.md#waiting-runs)). In the Run tab it shows as
  *Waiting*; send `{"type": "quote.replied", "data": {"key": "<the customer>"}}` on the **Events** page to try the reply.
- **Structured output.** Data extraction, Product description writer, Lead qualification, Meeting notes and action
  items and Resume screening return validated JSON in the `structured_response` field.
- **Web access.** Web research agent can call Wikipedia's public search API; each call waits for your approval
  (remove the tool from **Tools that need approval** on the Researcher block to let it search freely).
- **No database access.** SQL query helper only drafts a query from the schema you paste; you run it yourself.
- **Memory.** RAG Q&A answers from the `knowledge` memory space: fill it first on the [Memory](memory.md) page.
  Memory concierge remembers, recalls and forgets per person; Resilient research agent keeps findings with memory
  tools.
- **Events and code.** Support inbox from email events starts on an event your handler receives; send one from the
  [Events](workspace.md) page to try it. Lead scoring with research and Daily metrics report run your workspace code.
- **Safety.** Support inbox checks for prompt injection and hides personal data; Content safety gate checks for
  harmful content and the output format.
- **Other frameworks.** Every template exports as a project (see [Export and deploy](export.md)); exports to other
  agent frameworks list what does not carry over before you download.
