# Template gallery

On **Home**, under **…or start from a template**, the gallery holds 25 ready-to-run workflows for common jobs. Search
by name, description or tag (for example `email`, `sql` or `approval`), or click a category chip to narrow the list.
Each card shows the category, a short description, tags, how many blocks the workflow has and whether it is a
**beginner** or an **intermediate** example. Clicking a card (or focusing it and pressing **Enter**) creates the
workflow, named after the template unless you typed a name, and opens it in the editor.

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

## Customer support

| Template | What it does | Level |
|---|---|---|
| **Email reply with approval** | Drafts a reply to an email in the tone you choose; a person approves or edits it before it is marked ready to send. | beginner |
| **Customer FAQ bot** | A support chatbot that answers only from the FAQ in its instructions and hands anything else to a person. Replace the sample FAQ with your own. | beginner |
| **Feedback sentiment triage** | Reads a piece of customer feedback, labels its sentiment and routes it: negative feedback gets a careful drafted reply, positive feedback a thank-you. | beginner |
| **Support triage** | Classify a ticket's urgency; urgent tickets are escalated and wait for a manager, the rest get a drafted reply. | intermediate |

## Content

| Template | What it does | Level |
|---|---|---|
| **Blog post writer** | Outline, then draft, then an editor's review: a rejected draft is revised and comes back for review until the editor approves it. | intermediate |
| **Social media posts** | Turns one announcement into a LinkedIn post, a post for X and an Instagram caption, one step per network. | beginner |
| **Translation with glossary** | Translates text into another language using your glossary for brand and product terms, then checks that every glossary term was used. | beginner |

## Sales

| Template | What it does | Level |
|---|---|---|
| **Lead qualification** | Scores an inbound lead (need, budget, timeline) as structured output; hot leads get a drafted first email, the rest go to the nurture list. | intermediate |
| **Product description writer** | Writes shop-ready product copy (headline, description, bullet points, SEO keywords) from a list of facts, as structured output. | beginner |

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

## Operations

| Template | What it does | Level |
|---|---|---|
| **Policy compliance check** | Checks a document against your rules, quoting every passage that breaks one. Anything that is not clearly compliant goes to a reviewer. | intermediate |
| **Travel itinerary planner** | Plans a day-by-day itinerary within your budget from a short trip description, then writes a packing list for it. | beginner |

## Good to know

- **Human approval.** Approval workflow, Support triage, Email reply with approval, Blog post writer, Resume
  screening, Incident summary and Policy compliance check pause for a person; answer in the Run tab or the
  [Inbox](run-and-debug.md).
- **Structured output.** Data extraction, Product description writer, Lead qualification, Meeting notes and action
  items and Resume screening return validated JSON in the `structured_response` field.
- **Web access.** Web research agent can call Wikipedia's public search API; each call waits for your approval
  (remove the tool from **Tools that need approval** on the Researcher block to let it search freely).
- **No database access.** SQL query helper only drafts a query from the schema you paste; you run it yourself.
- **Memory.** RAG Q&A answers from the `knowledge` memory space: fill it first on the [Memory](memory.md) page.
- **Other frameworks.** Every template exports as a project (see [Export and deploy](export.md)); exports to other
  agent frameworks list what does not carry over before you download.
