// OSC/DCS strings, CSI sequences, charset picks, then any other two-byte escape.
// `$` catches a sequence the log's tail cut short.
const ESCAPES = /\x1b[\]PX^_][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b\[[0-?]*[ -/]*(?:[@-~]|$)|\x1b[()*+#][\x20-\x7e]?|\x1b[\x20-\x7e]?/g;

export const stripAnsi = (text: string): string => text.replace(ESCAPES, "");
