using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;

namespace Artpipe
{
    /// <summary>
    /// Writes JSON byte-identical to Phase 1's Python reports
    /// (<c>json.dumps(indent=2, sort_keys=True, ensure_ascii=False)</c> + "\n"):
    /// keys sorted ordinally, 2-space indent, LF, trailing newline, invariant
    /// culture. Same input, same bytes, so reports can be diffed and cached by hash.
    ///
    /// Hand-written on purpose: a general serializer (JsonUtility, Newtonsoft)
    /// doesn't sort keys and formats floats by culture or round-trip rules.
    /// Supports what reports need: dictionaries, lists, strings, bools,
    /// integers, doubles and null.
    /// </summary>
    public static class CanonicalJson
    {
        public static string Serialize(object value)
        {
            var sb = new StringBuilder();
            Write(sb, value, 0);
            sb.Append('\n');
            return sb.ToString();
        }

        static void Write(StringBuilder sb, object value, int depth)
        {
            switch (value)
            {
                case null: sb.Append("null"); break;
                case string s: WriteString(sb, s); break;
                case bool b: sb.Append(b ? "true" : "false"); break;
                case int or long: sb.Append(Convert.ToInt64(value).ToString(CultureInfo.InvariantCulture)); break;
                case double d: sb.Append(FormatDouble(d)); break;
                case IDictionary<string, object> dict: WriteObject(sb, dict, depth); break;
                case IEnumerable list: WriteArray(sb, list.Cast<object>().ToList(), depth); break;
                default: throw new ArgumentException("CanonicalJson: unsupported type " + value.GetType());
            }
        }

        static void WriteObject(StringBuilder sb, IDictionary<string, object> dict, int depth)
        {
            if (dict.Count == 0) { sb.Append("{}"); return; }
            sb.Append("{\n");
            var keys = dict.Keys.OrderBy(k => k, StringComparer.Ordinal).ToList();
            for (int i = 0; i < keys.Count; i++)
            {
                Indent(sb, depth + 1);
                WriteString(sb, keys[i]);
                sb.Append(": ");
                Write(sb, dict[keys[i]], depth + 1);
                if (i < keys.Count - 1) sb.Append(',');
                sb.Append('\n');
            }
            Indent(sb, depth);
            sb.Append('}');
        }

        static void WriteArray(StringBuilder sb, List<object> items, int depth)
        {
            if (items.Count == 0) { sb.Append("[]"); return; }
            sb.Append("[\n");
            for (int i = 0; i < items.Count; i++)
            {
                Indent(sb, depth + 1);
                Write(sb, items[i], depth + 1);
                if (i < items.Count - 1) sb.Append(',');
                sb.Append('\n');
            }
            Indent(sb, depth);
            sb.Append(']');
        }

        static void Indent(StringBuilder sb, int depth) => sb.Append(' ', depth * 2);

        // Matches Python's json.dumps for the values a report holds. Floats are
        // rounded upstream (6 digits, like Phase 1), so "R" never prints noise.
        static string FormatDouble(double d)
        {
            if (double.IsNaN(d) || double.IsInfinity(d))
                throw new ArgumentException("CanonicalJson: NaN/Infinity is not valid JSON");
            var s = d.ToString("R", CultureInfo.InvariantCulture);
            if (s.Contains("E")) s = s.Replace("E", "e");
            else if (!s.Contains(".")) s += ".0";  // Python prints 1.0, not 1
            return s;
        }

        static void WriteString(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }
    }
}
