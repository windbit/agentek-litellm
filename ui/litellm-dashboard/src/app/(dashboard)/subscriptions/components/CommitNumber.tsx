import { InputNumber } from "antd";
import { useState } from "react";

interface CommitNumberProps {
  value: number | null;
  min?: number;
  placeholder?: string;
  disabled?: boolean;
  onCommit: (value: number | null) => void;
}

/** A number field that saves on blur or Enter, not on every keystroke. */
export default function CommitNumber({ value, min, placeholder, disabled, onCommit }: CommitNumberProps) {
  const [draft, setDraft] = useState<number | null>(value);
  const [seen, setSeen] = useState<number | null>(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }

  const commit = () => {
    if (draft !== value) {
      onCommit(draft);
    }
  };

  return (
    <InputNumber
      value={draft}
      min={min}
      placeholder={placeholder}
      disabled={disabled}
      style={{ width: 76 }}
      onChange={setDraft}
      onBlur={commit}
      onPressEnter={commit}
    />
  );
}
