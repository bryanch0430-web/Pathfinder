import React, { Component, ErrorInfo, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallbackContent: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

class MarkdownErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.warn('Markdown render fallback activated:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="tv-md" style={{ whiteSpace: 'pre-wrap' }}>
          {this.props.fallbackContent}
        </div>
      );
    }
    return this.props.children;
  }
}

interface MarkdownMessageProps {
  content: string;
  className?: string;
}

export const MarkdownMessage: React.FC<MarkdownMessageProps> = React.memo(
  ({ content, className = '' }) => {
    if (!content) return null;
    const readableContent = content.replace(/<br\s*\/?\s*>/gi, '; ');

    return (
      <MarkdownErrorBoundary fallbackContent={content}>
        <div className={`tv-md ${className}`}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              ),
              // Replies sit under the page's h1: their top headings are level 2 for screen readers, whatever
              // the model wrote. Tags stay the same, so the styling doesn't change.
              h1: ({ children }) => <h1 aria-level={2}>{children}</h1>,
              h2: ({ children }) => <h2 aria-level={2}>{children}</h2>,
              h3: ({ children }) => <h3 aria-level={2}>{children}</h3>,
              h4: ({ children }) => <h4 aria-level={3}>{children}</h4>,
              h5: ({ children }) => <h5 aria-level={3}>{children}</h5>,
              h6: ({ children }) => <h6 aria-level={3}>{children}</h6>,
              table: ({ children }) => (
                <div className="tv-md__table">
                  <table>{children}</table>
                </div>
              ),
              code: ({ className, children, ...props }) => {
                const isInline = !className && !String(children).includes('\n');
                if (isInline) return <code className="tv-md__code">{children}</code>;
                return (
                  <pre className="tv-md__pre">
                    <code {...props}>{children}</code>
                  </pre>
                );
              },
            }}
          >
            {readableContent}
          </ReactMarkdown>
        </div>
      </MarkdownErrorBoundary>
    );
  }
);

MarkdownMessage.displayName = 'MarkdownMessage';
