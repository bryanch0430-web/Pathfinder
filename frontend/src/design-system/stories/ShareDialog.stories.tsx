import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Button } from "../atoms";
import { ShareDialog } from "../organisms";

const meta: Meta<typeof ShareDialog> = {
  title: "Organisms/Share dialog",
  component: ShareDialog,
};
export default meta;
type Story = StoryObj<typeof ShareDialog>;

const LINK = "http://localhost:5173/s/k3v9q2";
const EXPIRES = "2026-12-03T09:30:00Z";

export const LinkReady: Story = {
  render: () => {
    const [url, setUrl] = useState<string | null>(LINK);
    const [copied, setCopied] = useState(false);
    const [open, setOpen] = useState(true);
    return (
      <div style={{ padding: 24 }}>
        <Button variant="secondary" onClick={() => { setUrl(LINK); setOpen(true); }}>Share</Button>
        <ShareDialog
          open={open}
          onClose={() => setOpen(false)}
          url={url}
          expiresAt={url ? EXPIRES : null}
          copied={copied}
          revoked={!url}
          onCopy={() => setCopied(true)}
          onRevoke={() => setUrl(null)}
        />
      </div>
    );
  },
};

export const Creating: Story = {
  args: { open: true, url: null, loading: true, onClose: () => undefined },
};

export const TurnedOff: Story = {
  args: { open: true, url: null, revoked: true, onClose: () => undefined },
};

export const Failed: Story = {
  args: { open: true, url: null, error: "The trip could not be shared.", onClose: () => undefined },
};
