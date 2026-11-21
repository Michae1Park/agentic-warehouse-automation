import Panel from "./Panel";

export default function ChatPanel() {
  return (
    <Panel title="Operations Agent" area="chat">
      <div className="chat-log">
        <p className="muted">Agent connects in Step 2.</p>
      </div>
      <form className="chat-input" onSubmit={(e) => e.preventDefault()}>
        <input placeholder="e.g. Prepare order #1042 and move it to dispatch" disabled />
        <button disabled>Send</button>
      </form>
    </Panel>
  );
}
