import React from "react";

export function SearchField({
  id = "search-query",
  name = "query",
  defaultValue = "",
  placeholder = "Search canonical symbol or ID...",
  label = "Search",
}: {
  id?: string;
  name?: string;
  defaultValue?: string;
  placeholder?: string;
  label?: string;
}) {
  return (
    <div className="search-field-group">
      <label htmlFor={id} className="search-field-label">
        {label}
      </label>
      <div className="search-input-wrapper">
        <input
          id={id}
          type="search"
          name={name}
          defaultValue={defaultValue}
          placeholder={placeholder}
          className="search-field-input"
          autoComplete="off"
          spellCheck="false"
        />
        <button type="submit" className="search-submit-btn" aria-label="Submit search">
          Search
        </button>
      </div>
    </div>
  );
}
