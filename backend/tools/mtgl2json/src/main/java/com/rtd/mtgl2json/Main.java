package com.rtd.mtgl2json;

import com.fasterxml.jackson.core.JsonEncoding;
import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonGenerator;
import org.apache.lucene.document.Document;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexReader;
import org.apache.lucene.index.IndexableField;
import org.apache.lucene.store.FSDirectory;

import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * mtgl2json — read a Maltego .mtgl (Lucene-backed) graph and dump its
 * entities to JSON on stdout.
 *
 * Usage: java -jar mtgl2json.jar path/to/graph.mtgl
 *
 * Output shape (identical to what the Python side expects):
 *   {
 *     "entities": [
 *       { "type": "maltego.Domain", "fields": { "fqdn": "example.com", ... } },
 *       ...
 *     ],
 *     "meta": { "graph_version": "1.3", "client_version": "4.11.3" }
 *   }
 *
 * Errors go to stderr; non-zero exit on any failure.
 */
public final class Main {

    private static final String ENTITIES_SUBDIR = "Graphs/Graph1/DataEntities";

    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("usage: mtgl2json <file.mtgl>");
            System.exit(2);
        }
        Path input = Paths.get(args[0]);
        if (!Files.isRegularFile(input)) {
            System.err.println("not a file: " + input);
            System.exit(2);
        }

        Path tmp = null;
        try {
            tmp = Files.createTempDirectory("mtgl2json-");
            unzip(input, tmp);
            Path entitiesDir = tmp.resolve(ENTITIES_SUBDIR);
            if (!Files.isDirectory(entitiesDir)) {
                throw new IOException(
                    "no " + ENTITIES_SUBDIR + " in archive — not a Maltego .mtgl?");
            }
            List<Entity> entities = readEntities(entitiesDir);
            writeJson(System.out, entities);
        } catch (Exception e) {
            System.err.println("mtgl2json failed: " + e.getMessage());
            e.printStackTrace(System.err);
            System.exit(1);
        } finally {
            if (tmp != null) {
                deleteRecursively(tmp);
            }
        }
    }

    private static void unzip(Path zip, Path target) throws IOException {
        try (InputStream raw = Files.newInputStream(zip);
             ZipInputStream zin = new ZipInputStream(new BufferedInputStream(raw))) {
            ZipEntry ze;
            while ((ze = zin.getNextEntry()) != null) {
                Path out = target.resolve(ze.getName()).normalize();
                if (!out.startsWith(target)) {
                    throw new IOException("zip slip: " + ze.getName());
                }
                if (ze.isDirectory()) {
                    Files.createDirectories(out);
                } else {
                    Files.createDirectories(out.getParent());
                    Files.copy(zin, out, StandardCopyOption.REPLACE_EXISTING);
                }
                zin.closeEntry();
            }
        }
    }

    private static List<Entity> readEntities(Path indexDir) throws IOException {
        List<Entity> out = new ArrayList<>();
        try (FSDirectory dir = FSDirectory.open(indexDir);
             IndexReader reader = DirectoryReader.open(dir)) {
            int max = reader.maxDoc();
            for (int i = 0; i < max; i++) {
                Document doc = reader.document(i);
                if (doc == null) continue;
                Entity e = new Entity();
                for (IndexableField f : doc.getFields()) {
                    String v = f.stringValue();
                    if (v == null) continue;
                    e.addField(f.name(), v);
                }
                if (!e.fields.isEmpty()) {
                    out.add(e);
                }
            }
        }
        return out;
    }

    private static void writeJson(OutputStream target, List<Entity> entities) throws IOException {
        JsonFactory jf = new JsonFactory();
        try (JsonGenerator g = jf.createGenerator(target, JsonEncoding.UTF8)) {
            g.writeStartObject();
            g.writeArrayFieldStart("entities");
            for (Entity e : entities) {
                g.writeStartObject();
                g.writeStringField("type", e.typeOrEmpty());
                // Emit as an array of [name, value] pairs so that duplicate
                // (multi-valued) Lucene fields survive the JSON round trip.
                g.writeArrayFieldStart("fields");
                for (int i = 0; i < e.fields.size(); i += 2) {
                    g.writeStartArray();
                    g.writeString(e.fields.get(i));
                    g.writeString(e.fields.get(i + 1));
                    g.writeEndArray();
                }
                g.writeEndArray();
                g.writeEndObject();
            }
            g.writeEndArray();
            g.writeEndObject();
            g.flush();
        }
    }

    private static void deleteRecursively(Path p) {
        try (Stream<Path> s = Files.walk(p)) {
            s.sorted(Comparator.reverseOrder()).forEach(x -> {
                try { Files.deleteIfExists(x); } catch (IOException ignored) {}
            });
        } catch (IOException ignored) {}
    }

    /**
     * A single Maltego entity — a flat name/value list rather than a map,
     * because Lucene can carry duplicate field names (multi-valued fields)
     * and we want to preserve every value for the Python side to reduce.
     */
    private static final class Entity {
        final List<String> fields = new ArrayList<>();

        void addField(String name, String value) {
            fields.add(name);
            fields.add(value);
        }

        String typeOrEmpty() {
            // Maltego encodes each Lucene field's type in the name using
            // a "[…]" suffix, so the entity-type carrier is literally
            // "type[string]". Strip the suffix before comparing.
            for (int i = 0; i < fields.size(); i += 2) {
                String n = stripTypeSuffix(fields.get(i));
                if ("type".equalsIgnoreCase(n) || "entityType".equalsIgnoreCase(n)) {
                    return fields.get(i + 1);
                }
            }
            return "";
        }

        private static String stripTypeSuffix(String name) {
            int b = name.indexOf('[');
            return b < 0 ? name : name.substring(0, b);
        }
    }
}
