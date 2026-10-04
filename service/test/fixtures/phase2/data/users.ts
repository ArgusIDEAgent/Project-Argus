export function findUser(id: number) {
  return prisma.user.findUnique({ where: { id } });
}
