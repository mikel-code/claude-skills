export const meta = {
  name: 'llm-council',
  description: 'Five persona advisors deliberate independently, blind-review each other, and a chairman delivers a verdict',
  phases: [
    { title: 'Deliberate', detail: '5 advisors answer independently' },
    { title: 'Peer Review', detail: 'each advisor critiques the others, anonymized' },
    { title: 'Verdict', detail: 'chairman synthesizes agreements, splits, and next steps' },
  ],
}

// =====================================================================
// EDIT HERE to tune the council. Each advisor is { key, brief }.
// A brief is that advisor's entire personality: the subagent sees only
// its brief, the shared rules, and the question — nothing else.
// =====================================================================
const ADVISORS = [
  {
    key: 'contrarian',
    brief: `You are the Contrarian on an advisory council. Your only job is to find what will FAIL in whatever you are asked about: hidden risks, fatal flaws, second-order consequences, and the reasons similar attempts have failed for others. You are forbidden from being agreeable or reassuring. If something is genuinely strong, grant it one sentence and move on to what is weak. Steelman the failure case as hard as you can.`,
  },
  {
    key: 'first-principles',
    brief: `You are the First-Principles Thinker on an advisory council. Ignore how the question is framed. List the assumptions baked into it — including the ones the asker doesn't know they are making — and test each one. Rebuild the problem from fundamentals: what is actually true, what is actually wanted, what actually constrains the space. If the question is the wrong question, say so and state the right one before answering it.`,
  },
  {
    key: 'expansionist',
    brief: `You are the Expansionist on an advisory council. Your job is to find the upside the asker is not seeing: adjacent opportunities, bigger versions of the idea, unexpected combinations, second-order wins, and ways this could matter more than they think. Do not evaluate risk — another advisor covers that. Be concrete: name the specific opportunities, not vague "you could explore" gestures.`,
  },
  {
    key: 'outsider',
    brief: `You are the Outsider on an advisory council. You know nothing about the asker's field, industry, or conventions — and that is your value. React to the question the way an intelligent stranger would: question the jargon, the "everyone knows" assumptions, and the things insiders have stopped noticing. Ask the naive-sounding questions that are actually sharp, and answer them. Use plain language only.`,
  },
  {
    key: 'executor',
    brief: `You are the Executor on an advisory council. You care about exactly one thing: what the asker does Monday morning. No strategy essays. Turn the situation into a concrete, sequenced plan: the first action, the first week, what to measure, what to drop, and the smallest version that produces a real signal. Every item must be specific enough to start without further thought.`,
  },
]

// Accept args as an object, a JSON-encoded string, or a bare question string.
let input = args || {}
if (typeof input === 'string') {
  try {
    input = JSON.parse(input)
  } catch {
    input = { question: input }
  }
}
const { question, lite = false, cwd = null } = input
if (!question) throw new Error('No question was put before the council. Pass args.question.')

const SHARED_RULES = `
Rules:
- You are one of five independent advisors. You cannot see the others; answer entirely on your own.
- Take a clear position. No both-sides hedging — if it depends, say on what, then commit.
- Stay under ~400 words.
- Your final message IS your answer. Return the advice itself: no preamble, no sign-off.${
  cwd ? `\n- The question concerns the project at ${cwd}. Inspect its files first if that grounds your answer.` : ''
}`

phase('Deliberate')
log(lite ? 'Convening the council (lite — no peer review)…' : 'Convening the council…')

const raw = await parallel(
  ADVISORS.map((a) => () =>
    agent(`${a.brief}\n${SHARED_RULES}\n\nThe question before the council:\n${question}`, {
      label: a.key,
      phase: 'Deliberate',
    })
  )
)
const answers = ADVISORS.map((a, i) => ({ key: a.key, answer: raw[i] })).filter((x) => x.answer)
log(`${answers.length}/${ADVISORS.length} advisors reported`)
if (answers.length < 2) throw new Error('Too few advisors reported to hold a council.')

let reviews = []
if (!lite) {
  phase('Peer Review')
  const LABELS = ['A', 'B', 'C', 'D']
  // Math.random() is unavailable in workflow scripts, so anonymization uses a
  // deterministic rotation: reviewer i sees the others starting from position i,
  // so the label→author mapping differs for every reviewer. Persona voice may
  // still leak through the prose — inherent to any persona-based council.
  const rawReviews = await parallel(
    answers.map((self, i) => () => {
      const others = answers.filter((_, j) => j !== i)
      const rotated = others.map((_, k) => others[(k + i) % others.length])
      const labels = LABELS.slice(0, rotated.length)
      const packet = rotated
        .map((o, k) => `--- Response ${labels[k]} ---\n${o.answer}`)
        .join('\n\n')
      return agent(
        `You are one of five advisors on a council. The question before the council was:\n${question}\n\nYour own answer was:\n${self.answer}\n\nBelow are the other advisors' answers, anonymized as Responses ${labels.join(', ')}. Judge only the arguments, not any voice or style you think you recognize.\n\nFor each response, state: its strongest point, its weakest point or what it gets wrong, and what it misses entirely. Then rank the responses from most to least valuable to the asker (e.g. "${labels.join(' > ')}") with one line of justification.\n\nReturn only the critiques and the ranking.\n\n${packet}`,
        { label: `review:${self.key}`, phase: 'Peer Review' }
      ).then((r) => (r ? { key: self.key, review: r } : null))
    })
  )
  reviews = rawReviews.filter(Boolean)
  log(`${reviews.length} peer reviews collected`)
}

phase('Verdict')
const answersBlock = answers
  .map((a) => `=== ${a.key.toUpperCase()} ===\n${a.answer}`)
  .join('\n\n')
const reviewsBlock = reviews.length
  ? `\n\nBlind peer reviews (each advisor critiqued the others without attribution):\n\n${reviews
      .map((r) => `=== review written by ${r.key.toUpperCase()} ===\n${r.review}`)
      .join('\n\n')}`
  : ''

const verdict = await agent(
  `You are the Chairman of an advisory council. Five advisors with deliberately different mandates answered the question below, independently and blind to each other.${
    reviews.length
      ? ' Each then blind-reviewed the others; those critiques are included.'
      : ' No peer-review round was held this session — weigh the answers on their merits yourself.'
  }

Synthesize a verdict for the asker with exactly these sections:
1. **Where the council agrees** — points made independently by multiple advisors carry extra weight.
2. **Where it splits** — the real disagreements and what each side's case rests on.
3. **What survived scrutiny** — which arguments held up${reviews.length ? ' under peer review' : ''} and which fell apart.
4. **Verdict** — commit to a recommendation. Do not average the advisors into mush.
5. **Monday morning** — the concrete next steps, in order.

The question:
${question}

Advisor answers:

${answersBlock}${reviewsBlock}

Return only the synthesis, formatted with those five sections.`,
  { label: 'chairman', phase: 'Verdict' }
)

return { verdict, answers, lite }
