import { InputNumber } from "antd";
import { useState } from "react";

interface CommitNumberProps {
  value: number | null;
  min?: number;
  placeholder?: string;
  disabled?: boolean;
  /** Rejects when saving failed; the field then shows the saved value again. */
  onCommit: (value: number | null) => Promise<unknown>;
  allowEmpty?: boolean;
}

/** A number field that saves on blur or Enter, not on every keystroke. */
export default function CommitNumber({
  value,
  min,
  placeholder,
  disabled,
  onCommit,
  allowEmpty = true,
}: CommitNumberProps) {
  const [draft, setDraft] = useState<number | null>(value);
  const [seen, setSeen] = useState<number | null>(value);
  if (seen !== value) {
    setSeen(value);
    setDraft(value);
  }

  const commit = async () => {
    if (draft === null && !allowEmpty) {
      setDraft(value);
      return;
    }
    if (draft === value) {
      return;
    }
    try {
      await onCommit(draft);
    } catch {
      setDraft(value);
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
