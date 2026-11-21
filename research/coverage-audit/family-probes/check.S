.text
.globl check
check:
 push %r12
 mov $123,%r12
 call entry@PLT
 cmp $123,%r12
 setne %al
 movzbl %al,%eax
 pop %r12
 ret
.section .note.GNU-stack,"",@progbits
