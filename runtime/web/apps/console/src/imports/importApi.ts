import { ApiError, createApi, errorFromAnswer, type Api } from "../api/client";
import { projectPath } from "../projects/queries";
import type { ImportApi } from "./send";

// XMLHttpRequest, not fetch: it is the one way a page learns how much of an
// upload has left. No timeout -- a large archive on a slow link takes as long
// as it takes, and a dead connection errors out on its own.
function sendArchive(url: string, archive: Blob, onProgress: (sent: number, total: number) => void): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/x-tar");
    xhr.upload.onprogress = (event) => onProgress(event.loaded, event.lengthComputable ? event.total : archive.size);
    // Progress events are optional (a service worker in the way sends none);
    // this one marks the body fully sent, so the dialog can say it's unpacking.
    xhr.upload.onload = () => onProgress(archive.size, archive.size);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(undefined);
      else reject(errorFromAnswer(xhr.status, xhr.responseText));
    };
    xhr.onerror = () => reject(new ApiError("unreachable", "Eggie's service isn't answering", 0));
    xhr.send(archive);
  });
}

// Deleting a project waits on `compose down`, which outlasts the shared
// client's 10 s.
export function createImportApi(
  client: Api = createApi((input, init) => fetch(input, init), { timeoutMs: 120_000 }),
  send = sendArchive,
): ImportApi {
  return {
    create: (id) => client.post("/api/projects", { id }),
    // A plain DELETE keeps the folder and volumes; a replace means emptied.
    remove: (id) => client.del(`${projectPath(id)}?purge=true`),
    send: (id, archive, onProgress) => send(`${projectPath(id)}/files`, archive, onProgress),
  };
}
