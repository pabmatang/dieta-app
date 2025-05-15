// server.js (o app.js, o index.js dependiendo de tu estructura)

const express = require("express");
const bcrypt = require("bcryptjs");
const jwt = require("jsonwebtoken");
const User = require("./models/User"); // Importa tu modelo de usuario
const app = express();

// Middleware para parsear el cuerpo de las solicitudes
app.use(express.json());

// Ruta de registro
app.post("/register", async (req, res) => {
  const { email, password } = req.body;

  try {
    // Verifica si el usuario ya existe
    const userExists = await User.findOne({ email });
    if (userExists) {
      return res.status(400).send({ message: "El usuario ya existe." });
    }

    // Hashea la contraseña antes de almacenarla
    const hashedPassword = await bcrypt.hash(password, 10);

    // Crea un nuevo usuario
    const newUser = new User({ email, password: hashedPassword });
    await newUser.save();

    // Responde con éxito
    res.status(201).send({ message: "Usuario registrado exitosamente." });
  } catch (error) {
    res.status(500).send({ message: "Error al registrar el usuario.", error });
  }
});

// Ruta de login (para que tengas un ejemplo completo)
app.post("/login", async (req, res) => {
  const { email, password } = req.body;

  try {
    const user = await User.findOne({ email });
    if (!user) {
      return res.status(400).send({ message: "Usuario no encontrado." });
    }

    // Verifica la contraseña
    const isMatch = await bcrypt.compare(password, user.password);
    if (!isMatch) {
      return res.status(400).send({ message: "Contraseña incorrecta." });
    }

    // Crea un JWT
    const token = jwt.sign({ userId: user._id }, "secrect_key", { expiresIn: "1h" });

    // Devuelve el token
    res.json({ token });
  } catch (error) {
    res.status(500).send({ message: "Error al iniciar sesión.", error });
  }
});

// Inicia el servidor
app.listen(8000, () => {
  console.log("Servidor corriendo en http://localhost:8000");
});
