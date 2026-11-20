/*
 * AuthInput — v2 form field.
 *
 * Label above, helper/error below, focus resolves the hairline to full ink
 * with a soft ring. Password fields carry a mono SHOW/HIDE toggle.
 */
import React, { useId, useState } from 'react';

interface AuthInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string;
  hint?: string;
}

export const AuthInput: React.FC<AuthInputProps> = ({
  label,
  type = 'text',
  id,
  error,
  hint,
  required,
  className = '',
  ...props
}) => {
  const [reveal, setReveal] = useState(false);
  const reactId = useId();

  const isPassword = type === 'password';
  const inputType = isPassword && reveal ? 'text' : type;
  const inputId = id ?? `field-${reactId}`;
  const describedBy = error ? `${inputId}-err` : hint ? `${inputId}-hint` : undefined;

  return (
    <div className="tv-field">
      <label htmlFor={inputId} className="tv-field__label">
        {label}
        {required && <span className="tv-field__req"> *</span>}
      </label>

      <div className="tv-field__wrap">
        <input
          id={inputId}
          type={inputType}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={`tv-input ${error ? 'has-error' : ''} ${className}`}
          style={isPassword ? { paddingRight: '4.25rem' } : undefined}
          {...props}
        />

        {isPassword && (
          <button
            type="button"
            className="tv-field__reveal"
            onClick={() => setReveal((v) => !v)}
            aria-label={reveal ? 'Hide password' : 'Show password'}
          >
            {reveal ? 'Hide' : 'Show'}
          </button>
        )}
      </div>

      {error ? (
        <span id={`${inputId}-err`} className="tv-field__error" role="alert">
          {error}
        </span>
      ) : (
        hint && (
          <span id={`${inputId}-hint`} className="tv-meta" style={{ fontSize: '0.72rem' }}>
            {hint}
          </span>
        )
      )}
    </div>
  );
};
