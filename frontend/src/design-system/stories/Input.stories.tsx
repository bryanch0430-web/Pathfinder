import type { Meta, StoryObj } from "@storybook/react";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "../index";
import { useState } from "react";

const meta: Meta = { title: "Atoms/Inputs" };
export default meta;
type Story = StoryObj;

export const Playground: Story = {
  render: () => {
    const [checked, setChecked] = useState(true);
    const [text, setText] = useState("Kyoto");
    return (
      <div className="pf-card" style={{ padding: 24, maxWidth: 520 }}>
        <div className="pf-stack pf-stack--md">
          <Field label="Destination" required hint="City and country is most reliable">
            <Input value={text} onChange={(event) => setText(event.target.value)} placeholder="Kyoto, Japan" />
          </Field>
          <Field label="Search" htmlFor="search">
            <Input id="search" iconLeft="search" placeholder="Search saved trips" />
          </Field>
          <Field label="Currency">
            <Select defaultValue="JPY">
              <option>USD</option>
              <option>JPY</option>
              <option>HKD</option>
            </Select>
          </Field>
          <Field label="Notes" hint="Visible only to you">
            <Textarea placeholder="Slow pace, no early starts." />
          </Field>
          <Field label="Invalid state" error="End date must not precede start date">
            <Input defaultValue="2027-04-01" invalid />
          </Field>
          <Checkbox label="Lock this stop" defaultChecked />
          <Switch label="Include community notes" checked={checked} onChange={setChecked} />
        </div>
      </div>
    );
  },
};
