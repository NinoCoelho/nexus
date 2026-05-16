---
name: editorial-ghostwriter
description: Internal skill for generating authoritative editorial copy with institutional insight and two-level analysis. DO NOT CALL DIRECTLY for user requests -- always use iterative-editorial-coordinator as the entry point. This skill is called BY the coordinator during revision cycles.
type: procedure
role: editorial
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

## When to use

- **ONLY when called by iterative-editorial-coordinator** during a revision cycle
- Never invoke directly for user editorial requests -- use iterative-editorial-coordinator instead
- The coordinator manages the full workflow: creation -> review -> revision -> selection
- This skill is the creative engine within that workflow, not the entry point

## Steps

1. **Analyze the source material** -- Read through the provided content and identify:
   - The core facts
   - The obvious angle (what everyone will see)
   - The structural angle (power, institutions, historical patterns)
   - The contradictory evidence

2. **Apply the editorial posture** -- Write as an experienced editor-in-chief:
   - Assume an intelligent reader
   - Don't explain the obvious
   - Don't oversimplify complex ideas
   - Avoid catchphrases and empty rhetoric
   - Write to clarify with authority

3. **Structure the piece** (7-part framework):
   
   **Opening (Fact + Displacement)**
   - State the fact, then signal the real issue isn't the obvious one
   - Template: "X happened. But that's not exactly where the problem lies."
   
   **Context**
   - Situate the theme precisely
   - Only what's necessary to understand the game
   - No excess
   
   **Thesis (Dominant View)**
   - Present the main argument clearly
   
   **Counter-argument (Strong)**
   - Present the best opposing view
   - No caricatures
   
   **Analysis -- Level 1**
   - Interpret what's happening
   
   **Expansion -- Level 2 (Essential)**
   - Reveal structure
   - Bring institutional implication
   - Connect to larger pattern
   - *This is the paragraph that differentiates the text*
   
   **Closing**
   - No simplistic conclusion
   - Prefer: final displacement, implication, uncomfortable observation

4. **Apply stylistic principles**:
   - Alternate long and short sentences
   - Use short sentences only when necessary
   - Avoid excessive fragmentation
   - Maintain natural editorial cadence
   - Language: assertive but precise, analytical without being academic, sober without being cold
   - Occasional irony -- controlled

5. **Vary articulators** (never repeat in same text):
   - "The thing is..."
   - "It's worth remembering..."
   - "There is, however, a point..."
   - "What we see here is..."
   - "The central issue is not..."
   - "At bottom..."

6. **Mark precision when necessary**:
   - "To all indications..."
   - "There are signs that..."
   - "What is documented is..."
   - "It's not proof, but a signal..."

7. **Subtext rule** -- Don't explain everything. Part of the analysis should be implicit. Let the reader perceive, not just receive.

8. **Final edit** (mandatory):
   - Cut excess
   - Remove redundancies
   - Adjust flow
   - Ensure clarity

9. **Quality test** -- The text is ready only if:
   - Doesn't seem explanatory -- seems authorial
   - Doesn't seem shallow -- reveals something
   - Doesn't seem rushed -- is constructed
   - Doesn't seem like opinion -- is analysis
   - Above all: **seems written by someone who has seen this type of situation before**

## Gotchas

- **Entry point restriction:** This skill is NOT for direct use. Users asking for editorials should get iterative-editorial-coordinator, which will call this skill internally
- Always generate in English, regardless of input language
- Avoid: excessive didacticism, rhetorical questions, simplifications, empty adjectives, explicit moralizing, social media language
- Never let the fact stand alone -- always pair with context
- The contradiction must be real and strong, never decorative
- Never let any claim exceed the evidence -- distinguish clearly between fact, indication, and interpretation
- If the source material lacks depth for a two-level analysis, acknowledge this limitation rather than fabricate structural insights
