import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { Button, Dialog, Drawer, Heading, Input, Text } from "../atoms";

const meta: Meta = { title: "Atoms/Overlays" };
export default meta;
type Story = StoryObj;

export const DialogPlayground: Story = {
  render: () => {
    const [open, setOpen] = useState(false);
    return (
      <div style={{ padding: 24 }}>
        <Button variant="primary" onClick={() => setOpen(true)}>Open dialog</Button>
        <Dialog
          open={open}
          onClose={() => setOpen(false)}
          title="Change the trip dates"
          footer={
            <>
              <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="primary" onClick={() => setOpen(false)}>Apply change</Button>
            </>
          }
        >
          <Text size="sm" tone="secondary">Changing the length rebuilds the day shape. Confirmed stops are carried across.</Text>
          <label className="pf-field">
            <span className="pf-field__label">New start</span>
            <Input type="date" defaultValue="2027-04-10" />
          </label>
        </Dialog>
      </div>
    );
  },
};

export const DrawerPlayground: Story = {
  render: () => {
    const [open, setOpen] = useState(false);
    return (
      <div style={{ padding: 24 }}>
        <Button variant="secondary" onClick={() => setOpen(true)}>Open case drawer</Button>
        <Drawer open={open} onClose={() => setOpen(false)} title="Case hk02">
          <Heading level={5}>Budget constraint failed</Heading>
          <Text size="sm" tone="secondary">
            The plan returned a reference hotel and flight above the stated budget. The gap was
            recorded and shown; the plan was not silently accepted.
          </Text>
          <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Close</Button>
        </Drawer>
      </div>
    );
  },
};
