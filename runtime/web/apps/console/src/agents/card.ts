import { SSH_FACTS, type Connect, type Guide } from "./catalog";

export interface CardRow { label: string; value: string; copy: boolean }

type Fact = (typeof SSH_FACTS)[number];

// The API's own values, for a guest that could not say who its user is. Only
// the user has no fallback.
const FALLBACK: Record<Fact, string | null> = { host: "127.0.0.1", port: "39022", user: null, key_file: "~/.lima/_config/user" };
const UNKNOWN_USER = "your Mac user name";

// Each agent's form asks for the same facts in its own shape (Claude Code
// wants "user@host" in one box), so the manifest's template decides the row.
export function cardRows(guide: Guide, connect: Connect | undefined): CardRow[] | null {
  if (!guide.card) return null;
  const ssh = connect?.ssh;
  const facts: Record<Fact, string | null> = ssh
    ? { host: ssh.host, port: String(ssh.port), user: ssh.user, key_file: ssh.key_file }
    : FALLBACK;
  return guide.card.map(({ label, value }) => {
    let known = true;
    const filled = value.replace(/\{([^}]*)\}/g, (_, name: Fact) => {
      const fact = facts[name];
      if (fact !== null) return fact;
      known = false;
      return UNKNOWN_USER;
    });
    // Copying "your Mac user name@127.0.0.1" would paste a broken address.
    return { label, value: filled, copy: known };
  });
}
