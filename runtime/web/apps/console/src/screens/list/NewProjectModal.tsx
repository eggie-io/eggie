import { useId, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { Button, Modal, TextField } from "@eggie/ui";
import { ApiError } from "../../api/client";
import { useCreateProject } from "../../projects/queries";
import { slugify } from "../../projects/slugify";

export function NewProjectModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const create = useCreateProject();
  const navigate = useNavigate();
  const slug = slugify(name);
  const formId = useId();

  const close = () => {
    setName("");
    create.reset();
    onClose();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!slug || create.isPending) return;
    create.mutate(name, {
      onSuccess: (created) => {
        close();
        navigate(`/p/${encodeURIComponent(created.id)}`);
      },
    });
  };

  const error = create.error
    ? create.error instanceof ApiError && create.error.code === "project_exists"
      ? `There's already a project called ${slug}.`
      : create.error.message
    : undefined;

  return (
    <Modal
      open={open}
      onClose={close}
      title="Name your project"
      footer={
        <>
          <Button variant="ghost" onClick={close}>Keep it for later</Button>
          <Button type="submit" form={formId} variant="primary" disabled={!slug || create.isPending}>Make it</Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit}>
        <TextField
          label="Name"
          value={name}
          autoFocus
          onChange={(value) => {
            setName(value);
            if (create.isError) create.reset();
          }}
          hint={slug ? <>It'll be called <strong>{slug}</strong></> : undefined}
          error={error}
        />
      </form>
    </Modal>
  );
}
