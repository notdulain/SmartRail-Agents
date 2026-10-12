import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const components: Components = {
  a({ node: _node, ...props }) {
    return <a {...props} target="_blank" rel="noopener noreferrer" />;
  },
  img({ node: _node, ...props }) {
    return <img {...props} loading="lazy" />;
  },
  table({ node: _node, ...props }) {
    return (
      <div className="markdown-table-scroll">
        <table {...props} />
      </div>
    );
  },
};

export function MarkdownContent({ text }: { text: string }) {
  // Keep the default URL filtering and do not enable raw HTML from model or user messages.
  return (
    <div className="markdown-content">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>{text}</Markdown>
    </div>
  );
}
