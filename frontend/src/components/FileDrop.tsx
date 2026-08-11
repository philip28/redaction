import { useRef, useState } from "react";
import { useI18n } from "../i18n";

interface Props {
  accept: string;
  multiple?: boolean;
  files: File[];
  onChange: (files: File[]) => void;
  title: string;
  hint: string;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function FileDrop({ accept, multiple = true, files, onChange, title, hint }: Props) {
  const { t } = useI18n();
  const [active, setActive] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const add = (incoming: FileList | null) => {
    if (!incoming?.length) return;
    const list = Array.from(incoming);
    onChange(multiple ? [...files, ...list] : list.slice(0, 1));
  };

  return (
    <div>
      <div
        className="drop"
        data-active={active}
        onDragOver={(e) => {
          e.preventDefault();
          setActive(true);
        }}
        onDragLeave={() => setActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setActive(false);
          add(e.dataTransfer.files);
        }}
      >
        <h3>{title}</h3>
        <p>{hint}</p>
        <button type="button" className="btn-quiet" onClick={() => input.current?.click()}>
          {t("drop.choose")}
        </button>
        <input
          ref={input}
          type="file"
          accept={accept}
          multiple={multiple}
          hidden
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {files.length > 0 && (
        <ul className="filelist">
          {files.map((file, index) => (
            <li className="filerow" key={`${file.name}-${index}`}>
              <span className="name">{file.name}</span>
              <span className="size">{formatSize(file.size)}</span>
              <button
                type="button"
                className="btn-link"
                onClick={() => onChange(files.filter((_, i) => i !== index))}
                aria-label={t("drop.removeAria", { name: file.name })}
              >
                {t("drop.remove")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
