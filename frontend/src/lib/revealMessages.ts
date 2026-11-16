// The Reveal message wording, kept in one place so it is easy to edit. DRAFT Croatian copy:
// the Organizer approves the final text. The vocative form goes in greetings; the accusative
// form follows "daruješ" (both come from the list file, so no declension happens in code).

export function firstTimeMessage(vocativeName: string, receiverAccusative: string): string {
  return `Ho ho ho, ${vocativeName}! 🎁 Ove godine daruješ… ${receiverAccusative}!`;
}

export function reminderMessage(vocativeName: string, receiverAccusative: string): string {
  return `Podsjetnik, ${vocativeName}: ti daruješ ${receiverAccusative} 🤫`;
}
