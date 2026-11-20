import { useState, type FormEvent } from "react";
import { Navigate, useParams } from "react-router";
import { Button, Card, Modal, Notice, TextField } from "@eggie/ui";
import { customRows, eggieNote, type AddressList, type CustomAddress } from "../../domains/domains";
import { useAddDomain, useAddresses, useRemoveDomain } from "../../domains/queries";
import { actionError } from "../../projects/copy";
import { useSite } from "../../site/SiteContext";
import { DomainRow } from "./DomainRow";
import { TitleRow } from "./TitleRow";
import s from "./DomainPage.module.css";

function Addresses({ id, list, onRemove }: { id: string; list: AddressList; onRemove: (a: CustomAddress) => void }) {
  const eggie = list.addresses.find((a) => a.kind === "eggie");
  return (
    <ul className={s.rows}>
      {customRows(list).map((a) => <DomainRow key={a.hostname} id={id} address={a} onRemove={() => onRemove(a)} />)}
      {eggie && (
        <li className={s.row}>
          <div className={s.line}>
            <span className={s.host}>{eggie.hostname}</span>
            <span className={s.note}>{eggieNote(list)}</span>
          </div>
        </li>
      )}
    </ul>
  );
}

function RemoveModal({ id, address, onClose }: { id: string; address: CustomAddress | null; onClose: () => void }) {
  const remove = useRemoveDomain(id);
  // Kept while the dialog closes, so its title doesn't empty out.
  const [shown, setShown] = useState(address);
  if (address && address !== shown) setShown(address);
  const close = () => {
    remove.reset();
    onClose();
  };
  return (
    <Modal
      open={address !== null}
      onClose={close}
      title={`Remove ${shown?.display ?? ""}?`}
      footer={
        <>
          <Button onClick={close}>Cancel</Button>
          <Button
            variant="danger"
            disabled={remove.isPending}
            onClick={() => shown && remove.mutate(shown.hostname, { onSuccess: close })}
          >
            Remove
          </Button>
        </>
      }
    >
      <p className={s.modalText}>
        It stops opening this project right away. Remember to delete its DNS record where you bought the domain.
      </p>
      {remove.error && <Notice tone="danger">{actionError(remove.error)}</Notice>}
    </Modal>
  );
}

function AddForm({ id, list }: { id: string; list: AddressList }) {
  const add = useAddDomain(id);
  const [hostname, setHostname] = useState("");
  const [offer, setOffer] = useState<string | null>(null);
  const attached = (name: string) => customRows(list).some((a) => a.hostname === name);

  const send = (name: string) =>
    add.mutate(name, {
      onSuccess: (answer) => {
        setHostname("");
        setOffer(answer.suggested ?? null);
      },
    });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (hostname.trim() !== "") send(hostname.trim());
  };

  return (
    <>
      {offer && !attached(offer) && (
        <Notice tone="info">
          Also add {offer}?
          <div className={s.noticeActions}>
            <Button size="sm" variant="primary" disabled={add.isPending} onClick={() => send(offer)}>Add {offer}</Button>
          </div>
        </Notice>
      )}
      <form className={s.form} onSubmit={submit}>
        <TextField label="Domain" value={hostname} onChange={setHostname} placeholder="example.com" />
        <Button type="submit" variant="primary" icon="plus" className={s.formAction} disabled={add.isPending || hostname.trim() === ""}>
          Add domain
        </Button>
      </form>
      {add.error && <Notice tone="danger">{actionError(add.error)}</Notice>}
    </>
  );
}

// Keyed by project so the form, offer and hint state never carry over to another project.
export function DomainPage() {
  const { id = "" } = useParams();
  return <DomainContent key={id} id={id} />;
}

function DomainContent({ id }: { id: string }) {
  const site = useSite();
  const query = useAddresses(id, typeof site === "string");
  const [removing, setRemoving] = useState<CustomAddress | null>(null);

  if (site === null) return <Navigate to={`/p/${encodeURIComponent(id)}`} replace />;
  const list = query.data;

  return (
    <section className={s.page}>
      <TitleRow title="Custom domain" sub="Open this project on your own address." />
      {list ? (
        <>
          <Card as="section">
            <header className={s.cardHead}>
              <h2 className={s.cardTitle}>Addresses</h2>
            </header>
            <Addresses id={id} list={list} onRemove={setRemoving} />
          </Card>
          <AddForm id={id} list={list} />
          <RemoveModal id={id} address={removing} onClose={() => setRemoving(null)} />
        </>
      ) : query.isError ? (
        <Notice tone="danger">{actionError(query.error)}</Notice>
      ) : (
        site !== undefined && <p className={s.muted}>Looking up its addresses…</p>
      )}
    </section>
  );
}
