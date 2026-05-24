/**
 * Heuristic to check whether a transcription contains recognizable
 * English or Portuguese speech, as opposed to garbled / wrong-language
 * output from whisper interpreting background noise.
 *
 * Strategy: tokenise, then require at least one match against a curated
 * set of common EN/PT words (3+ chars to avoid tiny false-positive hits
 * like "a" or "e").  The list is intentionally small — we only need to
 * confirm the text looks like real speech, not classify the language.
 */

const COMMON_WORDS = new Set([
  // English — high-frequency content + function words (3+ chars)
  "the", "and", "for", "are", "but", "not", "you", "all", "can",
  "had", "her", "was", "one", "our", "out", "has", "have", "this",
  "that", "with", "from", "they", "been", "said", "each", "make",
  "like", "long", "look", "many", "some", "them", "than", "what",
  "when", "who", "will", "would", "about", "could", "other", "into",
  "just", "very", "also", "back", "over", "good", "year", "your",
  "how", "know", "take", "come", "want", "give", "use", "find",
  "tell", "ask", "work", "seem", "feel", "try", "leave", "call",
  "help", "need", "think", "get", "got", "going", "yes", "yeah",
  "hello", "please", "thanks", "sorry", "okay", "right", "well",
  "here", "there", "where", "which", "more", "much", "now", "new",
  "way", "see", "say", "day", "too", "any", "may", "did", "get",
  "let", "hey", "man", "sure", "thing", "really", "still", "mean",
  // Portuguese — high-frequency words (3+ chars)
  "não", "sim", "que", "com", "por", "para", "uma", "uns", "umas",
  "ele", "ela", "eles", "elas", "nos", "nós", "vocês", "vos",
  "mas", "como", "fosse", "está", "são", "sou", "era", "foram",
  "tem", "ter", "sei", "sua", "seu", "aqui", "ali", "ainda",
  "também", "só", "pois", "bem", "agora", "depois", "antes",
  "entre", "sobre", "até", "desde", "isso", "isto", "aquilo",
  "muito", "pouco", "mais", "menos", "tudo", "nada", "cada",
  "algo", "alguém", "ninguém", "nunca", "sempre", "já", "quando",
  "onde", "quem", "qual", "quanto", "porque", "então", "bom",
  "boa", "dia", "ola", "obrigado", "obrigada", "favor", "desculpe",
  "tchau", "fazer", "quer", "pode", "vai", "ter", "dar", "ver",
  "ir", "ser", "estar", "haver", "dizer", "saber", "querer",
  "preciso", "ajuda", "nome", "casa", "tempo", "vida", "mundo",
  "ano", "vez", "parte", "coisa", "problema", "trabalho",
]);

export function looksLikeSpeech(text: string): boolean {
  if (!text || !text.trim()) return false;

  const words = text
    .toLowerCase()
    .replace(/[^a-zàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ\s]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length >= 3);

  if (words.length < 1) return false;

  for (const w of words) {
    if (COMMON_WORDS.has(w)) return true;
  }

  return false;
}
