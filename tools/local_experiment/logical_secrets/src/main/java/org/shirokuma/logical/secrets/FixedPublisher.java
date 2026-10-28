package org.shirokuma.logical.secrets;

import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;
import java.util.TreeSet;

/** A fixed-target stdin publisher; deliberately has no path or command option. */
public final class FixedPublisher {
    private static final Path ROOT = Path.of("/etc/trino-private-secrets");
    private static final Path BOOTSTRAP = Path.of("/etc/trino-bootstrap");
    private static final Map<String, String> MUTABLE = Map.of(
            "password_db", "password.db", "rules_json", "rules.json", "guard_rego", "guard.rego");
    private static final String[] TARGETS = {"credentials.properties", "password.db", "rules.json", "guard.rego"};
    private static final int MAX_INPUT = 16 * 1024 * 1024;
    private static boolean mutationStarted;

    private static class Denial extends RuntimeException {}
    private static final class CasStale extends Denial {}

    private FixedPublisher() {}

    public static void main(String[] args) {
        try {
            require(args.length == 1);
            byte[] input = System.in.readNBytes(MAX_INPUT + 1);
            require(input.length <= MAX_INPUT);
            Properties request = new Properties() {
                @Override
                public synchronized Object put(Object key, Object value) {
                    require(!containsKey(key));
                    return super.put(key, value);
                }
            };
            try {
                request.load(new InputStreamReader(new java.io.ByteArrayInputStream(input), StandardCharsets.UTF_8));
            }
            catch (IllegalArgumentException exception) {
                throw new Denial();
            }
            switch (args[0]) {
                case "resolve" -> {
                    require(request.size() == 1 && request.containsKey("ref"));
                    validateReference(request.getProperty("ref"));
                    String value;
                    try {
                        value = new FileSecretProvider().resolveSecretValue(request.getProperty("ref"));
                    }
                    catch (IllegalArgumentException exception) {
                        throw new Denial();
                    }
                    System.out.println("status=OK\nref.sha256=" + digest(value.getBytes(StandardCharsets.UTF_8)));
                    requireOutput();
                }
                case "catalog" -> {
                    require(request.size() == 1 && request.containsKey("catalog"));
                    String name = request.getProperty("catalog");
                    require(name.matches("[A-Za-z][A-Za-z0-9_]{0,62}"));
                    Path catalog = Path.of("/var/trino-catalogs").resolve(name + ".properties");
                    String hash = "MISSING";
                    if (Files.isRegularFile(catalog)) {
                        Properties properties = new Properties();
                        try (Reader reader = Files.newBufferedReader(catalog, StandardCharsets.UTF_8)) {
                            properties.load(reader);
                        }
                        StringBuilder normalized = new StringBuilder();
                        for (String key : new TreeSet<>(properties.stringPropertyNames())) {
                            normalized.append(key).append('=').append(properties.getProperty(key)).append('\n');
                        }
                        hash = digest(normalized.toString().getBytes(StandardCharsets.UTF_8));
                    }
                    System.out.println("status=OK\ncatalog.sha256=" + hash);
                    requireOutput();
                }
                case "read" -> {
                    require(request.isEmpty());
                    receipts();
                }
                case "publish", "bootstrap" -> {
                    require(args[0].equals("publish") ? !request.isEmpty() : request.isEmpty());
                    Files.createDirectories(ROOT);
                    // This inode is stable: replacing a data file must never replace the lock.
                    try (FileChannel channel = FileChannel.open(ROOT.resolve(".publisher.lock"),
                            java.util.Set.of(StandardOpenOption.CREATE, StandardOpenOption.WRITE),
                            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
                            FileLock lock = channel.lock()) {
                        if (args[0].equals("bootstrap")) {
                            require(request.isEmpty());
                            bootstrap();
                        }
                        else {
                            publish(request);
                        }
                        receipts();
                    }
                }
                default -> throw new Denial();
            }
        }
        catch (CasStale exception) {
            fail(mutationStarted ? 24 : 22);
        }
        catch (Denial exception) {
            fail(mutationStarted ? 24 : 23);
        }
        catch (Throwable exception) {
            fail(24);
        }
    }

    private static void fail(int code) {
        // Never expose input, reference IDs, exception messages, or stack traces.
        System.err.println(switch (code) {
            case 22 -> "LOGICAL_SECRETS_CAS_STALE";
            case 23 -> "LOGICAL_SECRETS_DENIED";
            default -> "LOGICAL_SECRETS_UNKNOWN";
        });
        System.exit(code);
    }

    private static void validateReference(String reference) {
        try {
            FileSecretProvider.validateReference(reference);
        }
        catch (IllegalArgumentException exception) {
            throw new Denial();
        }
    }

    private static void publish(Properties request) throws Exception {
        require(!request.isEmpty());
        Properties merged = new Properties();
        Path credentials = ROOT.resolve("credentials.properties");
        if (Files.exists(credentials)) {
            try (Reader reader = Files.newBufferedReader(credentials, StandardCharsets.UTF_8)) {
                merged.load(reader);
            }
        }
        Map<String, byte[]> writes = new LinkedHashMap<>();
        boolean addedCredential = false;
        for (String key : request.stringPropertyNames()) {
            if (key.startsWith("credential.")) {
                String reference = key.substring("credential.".length());
                validateReference(reference);
                String value = request.getProperty(key);
                require(!value.isEmpty());
                String prior = merged.getProperty(reference);
                require(prior == null || prior.equals(value));
                merged.setProperty(reference, value);
                addedCredential |= prior == null;
            }
            else if (MUTABLE.containsKey(key)) {
                String name = MUTABLE.get(key);
                String expected = request.getProperty("expected." + name);
                require(expected != null && expected.matches("MISSING|[0-9a-f]{64}"));
                try {
                    writes.put(name, Base64.getDecoder().decode(request.getProperty(key)));
                }
                catch (IllegalArgumentException exception) {
                    throw new Denial();
                }
            }
            else if (key.startsWith("expected.")) {
                String name = key.substring("expected.".length());
                require(MUTABLE.containsValue(name));
                require(MUTABLE.entrySet().stream().anyMatch(entry ->
                        entry.getValue().equals(name) && request.containsKey(entry.getKey())));
            }
            else {
                throw new Denial();
            }
        }
        for (String name : writes.keySet()) {
            if (!request.getProperty("expected." + name).equals(currentDigest(ROOT.resolve(name)))) {
                throw new CasStale();
            }
        }
        // Validate every key and every CAS before publishing any file.
        if (addedCredential) {
            StringBuilder text = new StringBuilder();
            for (String key : new TreeSet<>(merged.stringPropertyNames())) {
                text.append(escape(key)).append('=').append(escape(merged.getProperty(key))).append('\n');
            }
            writes.put("credentials.properties", text.toString().getBytes(StandardCharsets.UTF_8));
        }
        for (Map.Entry<String, byte[]> write : writes.entrySet()) {
            atomicWrite(ROOT.resolve(write.getKey()), write.getValue());
        }
    }

    private static String escape(String value) {
        StringBuilder escaped = new StringBuilder();
        for (char c : value.toCharArray()) {
            switch (c) {
                case '\\' -> escaped.append("\\\\");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                case '\f' -> escaped.append("\\f");
                case ' ', '=', ':', '#', '!' -> escaped.append('\\').append(c);
                default -> escaped.append(c);
            }
        }
        return escaped.toString();
    }

    private static void bootstrap() throws Exception {
        Map<String, byte[]> copies = new LinkedHashMap<>();
        for (String name : TARGETS) {
            Path target = ROOT.resolve(name);
            Path source = BOOTSTRAP.resolve(name);
            if (!Files.exists(target)) {
                if (name.equals("credentials.properties") && !Files.exists(source)) {
                    copies.put(name, new byte[0]);
                }
                else {
                    copies.put(name, Files.readAllBytes(source));
                }
            }
        }
        for (Map.Entry<String, byte[]> copy : copies.entrySet()) {
            atomicWrite(ROOT.resolve(copy.getKey()), copy.getValue());
        }
    }

    private static void atomicWrite(Path target, byte[] bytes) throws Exception {
        // From the first attempted filesystem write onward, every error is an
        // uncertain outcome, including a later receipt or stream failure.
        mutationStarted = true;
        Path temporary = Files.createTempFile(ROOT, ".publish-", ".tmp",
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        try {
            try (FileChannel channel = FileChannel.open(temporary, StandardOpenOption.WRITE)) {
                java.nio.ByteBuffer buffer = java.nio.ByteBuffer.wrap(bytes);
                while (buffer.hasRemaining()) {
                    channel.write(buffer);
                }
                channel.force(true);
            }
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        }
        finally {
            Files.deleteIfExists(temporary);
        }
    }

    private static void receipts() throws Exception {
        StringBuilder result = new StringBuilder("status=OK\n");
        for (String name : TARGETS) {
            result.append("target.").append(name).append('=').append(currentDigest(ROOT.resolve(name))).append('\n');
        }
        System.out.print(result);
        requireOutput();
    }

    private static void requireOutput() throws java.io.IOException {
        if (System.out.checkError()) {
            throw new java.io.IOException();
        }
    }

    private static String currentDigest(Path target) throws Exception {
        return Files.exists(target) ? digest(Files.readAllBytes(target)) : "MISSING";
    }

    private static String digest(byte[] bytes) throws Exception {
        return java.util.HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    private static void require(boolean condition) {
        if (!condition) {
            throw new Denial();
        }
    }
}
