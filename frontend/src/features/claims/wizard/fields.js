// Small form primitives shared by wizard steps. Kept dumb on purpose:
// label + control + inline error, styled to the ClaimOS dark console look.
import { Paperclip } from 'lucide-react';

export function Field({ label, required = false, error, hint, children, testid }) {
  return (
    <div data-testid={testid}>
      <label className="block text-xs font-mono text-[#8892a4] uppercase tracking-wider mb-2">
        {label} {required && '*'}
      </label>
      {children}
      {error && <p className="text-xs font-mono text-[#ef4444] mt-1.5">{error}</p>}
      {!error && hint && <p className="text-xs font-mono text-[#4a5568] mt-1.5">{hint}</p>}
    </div>
  );
}

const BASE_INPUT =
  'w-full bg-[#0a0c12] border rounded-none px-4 py-3 text-sm text-[#e2e8f0] placeholder-[#2d3548] focus:outline-none focus:border-[#3b82f6] transition-colors duration-200';

function borderCls(error) {
  return error ? 'border-[#ef4444]' : 'border-[#232b3d]';
}

export function TextInput({ error = '', testid, ...props }) {
  return <input {...props} data-testid={testid} className={`${BASE_INPUT} ${borderCls(error)}`} />;
}

export function TextArea({ error = '', testid, ...props }) {
  return <textarea {...props} data-testid={testid} className={`${BASE_INPUT} ${borderCls(error)} min-h-[110px] resize-y`} />;
}

export function Select({ error = '', testid, children, ...props }) {
  return (
    <select {...props} data-testid={testid} className={`${BASE_INPUT} ${borderCls(error)}`}>
      {children}
    </select>
  );
}

export function FileInput({ files, onAdd, error }) {
  return (
    <div>
      <label
        className="flex items-center justify-center gap-2 border border-dashed border-[#232b3d] hover:border-[#3b82f6] hover:bg-[#3b82f6]/5 rounded-sm px-4 py-6 cursor-pointer transition-colors duration-200"
        data-testid="documents-input"
      >
        <Paperclip className="w-4 h-4 text-[#4a5568]" />
        <span className="text-sm font-mono text-[#8892a4]">Attach photos, reports, receipts (PDF/JPG/PNG)</span>
        <input
          type="file"
          multiple
          accept=".pdf,.jpg,.jpeg,.png"
          className="hidden"
          onChange={(e) => onAdd(Array.from(e.target.files || []))}
        />
      </label>
      {error && <p className="text-xs font-mono text-[#ef4444] mt-1.5">{error}</p>}
      {files.length > 0 && (
        <ul className="mt-2 space-y-1" data-testid="documents-list">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="text-xs font-mono text-[#7dd3fc] flex items-center gap-2">
              <Paperclip className="w-3 h-3" /> {f.name} ({Math.max(1, Math.round(f.size / 1024))} KB)
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
