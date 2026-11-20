import React, { useState, useRef, useEffect } from "react";
import { ArrowUpRightIcon, PlusIcon, CloseIcon } from "../home/v2/IconsV2";
import { useSpeechRecognition } from "../../hooks/useSpeechRecognition";

export interface ChatComposerProps {
  onSendMessage: (content: string, attachments?: File[]) => void;
  isLoading?: boolean;
  disabled?: boolean;
  placeholder?: string;
  initialValue?: string;
  /** The line under the composer; defaults to the keyboard hint. */
  hint?: string;
  /** "Plan" before there's a trip, "Send" once there is one. */
  sendLabel?: string;
}

export const ChatComposer: React.FC<ChatComposerProps> = ({
  onSendMessage,
  isLoading = false,
  disabled = false,
  placeholder = "Describe your voyage: destination, duration, budget, or preferred sights...",
  initialValue = "",
  hint = 'Enter to send · Shift + Enter for a new line',
  sendLabel = 'Plan',
}) => {
  const [input, setInput] = useState<string>(initialValue);
  const [attachments, setAttachments] = useState<File[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Real browser speech recognition hook
  const { isListening, isSupported, toggleListening } = useSpeechRecognition({
    onTranscript: (spokenText: string) => {
      if (!spokenText.trim()) return;
      setInput((prev) => {
        const trimmed = prev.trim();
        if (!trimmed) {
          return spokenText.trim();
        }
        return `${trimmed} ${spokenText.trim()}`;
      });
      if (textareaRef.current) {
        textareaRef.current.focus();
      }
    },
  });

  // Sync when initialValue changes from external prompt selection
  useEffect(() => {
    if (initialValue) {
      setInput(initialValue);
      if (textareaRef.current) {
        textareaRef.current.focus();
      }
    }
  }, [initialValue]);

  // Auto-resize textarea height to sensible limits
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
    }
  }, [input]);

  const handleSend = () => {
    const trimmed = input.trim();
    if ((!trimmed && attachments.length === 0) || isLoading || disabled) return;
    onSendMessage(trimmed, attachments);
    setInput("");
    setAttachments([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const selected = Array.from(e.target.files);
      setAttachments((prev) => [...prev, ...selected]);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleRemoveFile = (indexToRemove: number) => {
    setAttachments((prev) => prev.filter((_, idx) => idx !== indexToRemove));
  };

  const canSubmit =
    (input.trim().length > 0 || attachments.length > 0) &&
    !isLoading &&
    !disabled;

  return (
    <div className="tv-comp">
      <div className={`tv-comp__shell ${isListening ? 'is-listening' : ''}`}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={handleFileChange}
        />

        {attachments.length > 0 && (
          <div className="tv-comp__files">
            {attachments.map((file, idx) => (
              <span key={`${file.name}-${idx}`} className="tv-tag">
                <span className="tv-comp__filename">{file.name}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveFile(idx)}
                  aria-label={`Remove ${file.name}`}
                  className="tv-comp__filex"
                >
                  <CloseIcon width={11} height={11} />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* One unified row: [+] input [mic] [Plan] */}
        <div className="tv-comp__row">
          <button
            type="button"
            className="tv-iconbtn tv-comp__attach"
            onClick={() => fileInputRef.current?.click()}
            disabled={isLoading || disabled}
            aria-label="Attach a file"
            title="Attach a file"
          >
            <PlusIcon width={17} height={17} />
          </button>

          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={isLoading || disabled}
            className="tv-comp__input"
          />

          {isSupported && (
            <button
              type="button"
              className={`tv-iconbtn tv-comp__mic ${isListening ? 'is-active' : ''}`}
              onClick={toggleListening}
              disabled={isLoading || disabled}
              aria-pressed={isListening}
              aria-label={isListening ? 'Stop dictation' : 'Dictate'}
              title={isListening ? 'Stop dictation' : 'Dictate'}
            >
              <MicIcon width={17} height={17} />
            </button>
          )}

          <button
            type="button"
            className="tv-btn tv-btn--primary tv-btn--sm tv-comp__send"
            onClick={handleSend}
            disabled={!canSubmit}
          >
            <span>{isLoading ? 'Working' : sendLabel}</span>
            <ArrowUpRightIcon width={14} height={14} />
          </button>
        </div>
      </div>

      <p className="tv-meta tv-comp__hint">
        {isListening
          ? 'Listening — speak your changes'
          : hint}
      </p>
    </div>
  );
};

function MicIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <rect x="9" y="2.6" width="6" height="11" rx="3" />
      <path d="M5.5 11.2a6.5 6.5 0 0 0 13 0M12 17.7v3.7" />
    </svg>
  );
}
